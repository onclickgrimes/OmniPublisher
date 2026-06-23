import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.models.db import PublishJob, PublishPlatformStatus, SessionLocal
from app.models.schemas import PlatformStatus, PublishJobResponse, PublishRequest, TaskState


TERMINAL_PLATFORM_STATUSES = {"success", "error"}


def _model_dump(model, **kwargs):
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    return model.dict(**kwargs)


def _utc_now() -> datetime:
    return datetime.utcnow()


def _to_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _loads_json(value: str | None, fallback: Any):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class TaskManager:
    """
    Gerencia o estado das tarefas em memória para SSE e persiste jobs no SQLite.
    """

    def __init__(self):
        self.tasks_state: Dict[str, TaskState] = {}
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}

    def create_task(
        self,
        task_id: str,
        platforms: List[str],
        *,
        request: PublishRequest | None = None,
        mode: str = "immediate",
        status: str = "queued",
    ):
        """Inicializa uma task e, quando request é informado, persiste o job."""
        self._set_memory_task(task_id, platforms)
        if request:
            self._persist_job(task_id, request, platforms, mode=mode, status=status)

    def ensure_memory_task(self, task_id: str) -> TaskState | None:
        if task_id in self.tasks_state:
            return self.tasks_state[task_id]
        return self._hydrate_task_from_db(task_id)

    def get_task(self, task_id: str) -> TaskState | None:
        """Retorna o estado de uma task, hidratando do SQLite quando necessário."""
        return self.ensure_memory_task(task_id)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        """Inscreve-se para receber eventos de uma task (SSE)."""
        if task_id not in self._subscribers:
            self._subscribers[task_id] = []
        queue = asyncio.Queue()
        self._subscribers[task_id].append(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue):
        """Remove a inscrição de um cliente."""
        if task_id in self._subscribers and queue in self._subscribers[task_id]:
            self._subscribers[task_id].remove(queue)

    async def update_status(
        self,
        task_id: str,
        platform: str,
        status: str,
        progress: int = 0,
        error: str = None,
    ):
        """
        Atualiza o status de uma plataforma em uma task, persiste e notifica via SSE.
        """
        task_state = self.ensure_memory_task(task_id)
        if not task_state:
            return

        plat_status = task_state.platforms.get(platform)
        if not plat_status:
            return

        plat_status.status = status
        plat_status.progress = progress
        plat_status.error = error

        self._persist_platform_update(task_id, platform, status, progress, error)

        event_payload = {
            "task_id": task_id,
            "platform": platform,
            "status": status,
            "progress": progress,
            "error": error,
        }

        if task_id in self._subscribers:
            for queue in list(self._subscribers[task_id]):
                await queue.put(event_payload)

    def mark_job_running(self, task_id: str):
        now = _utc_now()
        db = SessionLocal()
        try:
            job = db.query(PublishJob).filter(PublishJob.id == task_id).first()
            if not job or job.status in {"success", "error", "canceled"}:
                return
            job.status = "running"
            job.started_at = job.started_at or now
            job.updated_at = now
            db.commit()
        finally:
            db.close()

    def get_publish_request(self, task_id: str) -> PublishRequest | None:
        db = SessionLocal()
        try:
            job = db.query(PublishJob).filter(PublishJob.id == task_id).first()
            if not job:
                return None
            return self._request_from_job(job)
        finally:
            db.close()

    def list_jobs(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        db = SessionLocal()
        try:
            query = db.query(PublishJob)
            if status:
                query = query.filter(PublishJob.status == status)
            jobs = query.order_by(PublishJob.created_at.desc()).limit(max(1, min(limit, 500))).all()
            return [self._job_to_dict(db, job) for job in jobs]
        finally:
            db.close()

    def get_job(self, task_id: str) -> dict[str, Any] | None:
        db = SessionLocal()
        try:
            job = db.query(PublishJob).filter(PublishJob.id == task_id).first()
            if not job:
                return None
            return self._job_to_dict(db, job)
        finally:
            db.close()

    def claim_due_jobs(self, limit: int = 20) -> list[str]:
        now = _utc_now()
        db = SessionLocal()
        try:
            jobs = (
                db.query(PublishJob)
                .filter(PublishJob.status == "queued")
                .filter(PublishJob.scheduled_at.isnot(None))
                .filter(PublishJob.scheduled_at <= now)
                .order_by(PublishJob.scheduled_at.asc())
                .limit(max(1, limit))
                .all()
            )
            job_ids = []
            for job in jobs:
                job.status = "running"
                job.started_at = job.started_at or now
                job.updated_at = now
                job_ids.append(job.id)
            db.commit()
            return job_ids
        finally:
            db.close()

    def fail_job(self, task_id: str, error: str):
        now = _utc_now()
        db = SessionLocal()
        try:
            job = db.query(PublishJob).filter(PublishJob.id == task_id).first()
            if job:
                job.status = "error"
                job.error = error
                job.finished_at = job.finished_at or now
                job.updated_at = now
                db.commit()
        finally:
            db.close()

    def _set_memory_task(self, task_id: str, platforms: List[str], created_at: datetime | None = None):
        platform_statuses = {
            plat: PlatformStatus(platform=plat, status="pending")
            for plat in platforms
        }
        self.tasks_state[task_id] = TaskState(
            task_id=task_id,
            platforms=platform_statuses,
            created_at=created_at or _utc_now(),
        )
        self._subscribers.setdefault(task_id, [])

    def _persist_job(
        self,
        task_id: str,
        request: PublishRequest,
        platforms: List[str],
        *,
        mode: str,
        status: str,
    ):
        now = _utc_now()
        payload = _model_dump(request)
        accounts = payload.get("accounts") or {}
        youtube_tags = payload.get("youtube_tags")

        db = SessionLocal()
        try:
            job = db.query(PublishJob).filter(PublishJob.id == task_id).first()
            if not job:
                job = PublishJob(id=task_id, created_at=now)
                db.add(job)

            job.mode = mode
            job.status = status
            job.video_path = request.video_path
            job.caption = request.caption
            job.accounts_json = json.dumps(accounts, ensure_ascii=False)
            job.youtube_title = request.youtube_title
            job.youtube_tags_json = json.dumps(youtube_tags or [], ensure_ascii=False)
            job.youtube_privacy = request.youtube_privacy
            job.instagram_format = request.instagram_format
            job.scheduled_at = _to_utc_naive(request.scheduled_at)
            job.updated_at = now
            job.error = None

            for platform in platforms:
                existing = (
                    db.query(PublishPlatformStatus)
                    .filter(PublishPlatformStatus.job_id == task_id)
                    .filter(PublishPlatformStatus.platform == platform)
                    .first()
                )
                if not existing:
                    existing = PublishPlatformStatus(
                        job_id=task_id,
                        platform=platform,
                        account_id=str(accounts.get(platform) or ""),
                    )
                    db.add(existing)
                existing.status = "pending"
                existing.progress = 0
                existing.error = None
                existing.updated_at = now

            db.commit()
        finally:
            db.close()

    def _hydrate_task_from_db(self, task_id: str) -> TaskState | None:
        db = SessionLocal()
        try:
            job = db.query(PublishJob).filter(PublishJob.id == task_id).first()
            if not job:
                return None
            rows = (
                db.query(PublishPlatformStatus)
                .filter(PublishPlatformStatus.job_id == task_id)
                .order_by(PublishPlatformStatus.platform.asc())
                .all()
            )
            task_state = TaskState(
                task_id=task_id,
                platforms={
                    row.platform: PlatformStatus(
                        platform=row.platform,
                        status=row.status,
                        progress=row.progress,
                        error=row.error,
                    )
                    for row in rows
                },
                created_at=job.created_at,
            )
            self.tasks_state[task_id] = task_state
            self._subscribers.setdefault(task_id, [])
            return task_state
        finally:
            db.close()

    def _persist_platform_update(
        self,
        task_id: str,
        platform: str,
        status: str,
        progress: int,
        error: str | None,
    ):
        now = _utc_now()
        db = SessionLocal()
        try:
            row = (
                db.query(PublishPlatformStatus)
                .filter(PublishPlatformStatus.job_id == task_id)
                .filter(PublishPlatformStatus.platform == platform)
                .first()
            )
            if row:
                row.status = status
                row.progress = progress
                row.error = error
                row.updated_at = now

            job = db.query(PublishJob).filter(PublishJob.id == task_id).first()
            if job:
                if status == "uploading" and job.status == "queued":
                    job.status = "running"
                    job.started_at = job.started_at or now
                job.updated_at = now

                rows = (
                    db.query(PublishPlatformStatus)
                    .filter(PublishPlatformStatus.job_id == task_id)
                    .all()
                )
                if rows and all(item.status in TERMINAL_PLATFORM_STATUSES for item in rows):
                    failed = [item for item in rows if item.status == "error"]
                    job.status = "error" if failed else "success"
                    job.finished_at = job.finished_at or now
                    job.error = "; ".join(
                        f"{item.platform}: {item.error}" for item in failed if item.error
                    ) or None

            db.commit()
        finally:
            db.close()

    def _request_from_job(self, job: PublishJob) -> PublishRequest:
        return PublishRequest(
            mode=job.mode,
            scheduled_at=job.scheduled_at,
            video_path=job.video_path,
            caption=job.caption,
            accounts=_loads_json(job.accounts_json, {}),
            youtube_title=job.youtube_title,
            youtube_tags=_loads_json(job.youtube_tags_json, []),
            youtube_privacy=job.youtube_privacy,
            instagram_format=job.instagram_format,
        )

    def _job_to_dict(self, db, job: PublishJob) -> dict[str, Any]:
        platforms = (
            db.query(PublishPlatformStatus)
            .filter(PublishPlatformStatus.job_id == job.id)
            .order_by(PublishPlatformStatus.platform.asc())
            .all()
        )
        payload = {
            "id": job.id,
            "task_id": job.id,
            "mode": job.mode,
            "status": job.status,
            "video_path": job.video_path,
            "caption": job.caption,
            "accounts": _loads_json(job.accounts_json, {}),
            "youtube_title": job.youtube_title,
            "youtube_tags": _loads_json(job.youtube_tags_json, []),
            "youtube_privacy": job.youtube_privacy,
            "instagram_format": job.instagram_format,
            "scheduled_at": job.scheduled_at,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "error": job.error,
            "platforms": [
                {
                    "platform": row.platform,
                    "account_id": row.account_id,
                    "status": row.status,
                    "progress": row.progress,
                    "error": row.error,
                    "updated_at": row.updated_at,
                }
                for row in platforms
            ],
        }
        return _model_dump(PublishJobResponse(**payload))


task_manager = TaskManager()
