import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List

from app.models.db import PublishJob, PublishJobEvent, PublishPlatformStatus, SessionLocal
from app.models.schemas import PlatformStatus, PublishJobResponse, PublishRequest, TaskState
from app.services.time_utils import to_utc_naive, utc_naive_to_app_aware, utc_now_naive


TERMINAL_JOB_STATUSES = {"success", "error", "canceled"}
TERMINAL_PLATFORM_STATUSES = {"success", "error", "canceled"}


def _model_dump(model, **kwargs):
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    return model.dict(**kwargs)


def _utc_now() -> datetime:
    return utc_now_naive()


def _loads_json(value: str | None, fallback: Any):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


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

    async def _notify_subscribers(self, task_id: str, event_payload: dict[str, Any]):
        if task_id in self._subscribers:
            for queue in list(self._subscribers[task_id]):
                await queue.put(event_payload)

    def _job_has_terminal_status(self, task_id: str, statuses: set[str] | None = None) -> bool:
        db = SessionLocal()
        try:
            job = db.query(PublishJob).filter(PublishJob.id == task_id).first()
            if not job:
                return False
            return job.status in (statuses or TERMINAL_JOB_STATUSES)
        finally:
            db.close()

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
        if status != "canceled" and self._job_has_terminal_status(task_id, {"canceled"}):
            return

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
            if not job or job.status in TERMINAL_JOB_STATUSES:
                return
            job.status = "running"
            job.started_at = job.started_at or now
            job.updated_at = now
            self._add_event(
                db,
                task_id,
                "job_running",
                "Job marcado como em execução.",
            )
            db.commit()
        finally:
            db.close()

    async def record_platform_warning(
        self,
        task_id: str,
        platform: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ):
        """
        Registra um aviso não fatal de plataforma, sem alterar o status do upload.
        """
        now = _utc_now()
        event_payload = {
            "platform": platform,
            **(payload or {}),
        }

        db = SessionLocal()
        try:
            job = db.query(PublishJob).filter(PublishJob.id == task_id).first()
            if not job or job.status == "canceled":
                return

            job.updated_at = now
            self._add_event(
                db,
                task_id,
                "platform_warning",
                message,
                event_payload,
            )
            db.commit()
        finally:
            db.close()

        await self._notify_subscribers(
            task_id,
            {
                "type": "warning",
                "task_id": task_id,
                "platform": platform,
                "status": self.tasks_state.get(task_id).platforms.get(platform).status
                if task_id in self.tasks_state and platform in self.tasks_state[task_id].platforms
                else None,
                "warning": message,
                "payload": event_payload,
            },
        )

    async def record_platform_result(
        self,
        task_id: str,
        platform: str,
        payload: dict[str, Any],
    ):
        """
        Persiste o retorno do provider para auditoria pós-upload.
        """
        now = _utc_now()
        event_payload = {
            "platform": platform,
            "result": payload,
        }

        db = SessionLocal()
        try:
            job = db.query(PublishJob).filter(PublishJob.id == task_id).first()
            if not job or job.status == "canceled":
                return

            job.updated_at = now
            self._add_event(
                db,
                task_id,
                "platform_result",
                f"{platform}: resultado do provider registrado.",
                event_payload,
            )
            db.commit()
        finally:
            db.close()

        await self._notify_subscribers(
            task_id,
            {
                "type": "result",
                "task_id": task_id,
                "platform": platform,
                "payload": event_payload,
            },
        )

    def get_publish_request(self, task_id: str) -> PublishRequest | None:
        db = SessionLocal()
        try:
            job = db.query(PublishJob).filter(PublishJob.id == task_id).first()
            if not job:
                return None
            return self._request_from_job(job)
        finally:
            db.close()

    def list_jobs(
        self,
        *,
        status: str | None = None,
        workspace_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        db = SessionLocal()
        try:
            query = db.query(PublishJob)
            if status:
                query = query.filter(PublishJob.status == status)
            if workspace_id:
                query = query.filter(PublishJob.workspace_id == workspace_id)
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
                self._add_event(
                    db,
                    job.id,
                    "scheduled_due",
                    "Job agendado vencido; execução iniciada pelo scheduler.",
                    {
                        "scheduled_at": (
                            utc_naive_to_app_aware(job.scheduled_at).isoformat()
                            if job.scheduled_at
                            else None
                        )
                    },
                )
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
                if job.status in TERMINAL_JOB_STATUSES:
                    return
                job.status = "error"
                job.error = error
                job.finished_at = job.finished_at or now
                job.updated_at = now

                rows = (
                    db.query(PublishPlatformStatus)
                    .filter(PublishPlatformStatus.job_id == task_id)
                    .all()
                )
                for row in rows:
                    if row.status not in TERMINAL_PLATFORM_STATUSES:
                        row.status = "error"
                        row.error = error
                        row.updated_at = now

                self._add_event(
                    db,
                    task_id,
                    "job_error",
                    error,
                )
                db.commit()
        finally:
            db.close()

    async def cancel_job(self, task_id: str, reason: str = "Job cancelado pelo usuário.") -> bool:
        now = _utc_now()
        changed_platforms: list[str] = []
        db = SessionLocal()
        try:
            job = db.query(PublishJob).filter(PublishJob.id == task_id).first()
            if not job:
                return False
            if job.status in {"success", "error", "canceled"}:
                return False

            job.status = "canceled"
            job.error = reason
            job.finished_at = job.finished_at or now
            job.updated_at = now

            rows = (
                db.query(PublishPlatformStatus)
                .filter(PublishPlatformStatus.job_id == task_id)
                .all()
            )
            for row in rows:
                if row.status not in TERMINAL_PLATFORM_STATUSES:
                    row.status = "canceled"
                    row.error = reason
                    row.updated_at = now
                    changed_platforms.append(row.platform)

            self._add_event(
                db,
                task_id,
                "job_canceled",
                reason,
            )
            db.commit()
        finally:
            db.close()

        task_state = self.ensure_memory_task(task_id)
        if task_state:
            for platform in changed_platforms:
                plat_status = task_state.platforms.get(platform)
                if plat_status:
                    plat_status.status = "canceled"
                    plat_status.error = reason
                    plat_status.progress = plat_status.progress or 0

                await self._notify_subscribers(
                    task_id,
                    {
                        "task_id": task_id,
                        "platform": platform,
                        "status": "canceled",
                        "progress": plat_status.progress if plat_status else 0,
                        "error": reason,
                    },
                )
        return True

    async def fail_stale_running_jobs(self, max_age_seconds: int, reason: str) -> list[str]:
        cutoff = _utc_now() - timedelta(seconds=max(1, max_age_seconds))
        failed_job_ids: list[str] = []
        db = SessionLocal()
        try:
            jobs = (
                db.query(PublishJob)
                .filter(PublishJob.status == "running")
                .filter(PublishJob.started_at.isnot(None))
                .filter(PublishJob.started_at <= cutoff)
                .all()
            )
            for job in jobs:
                job.status = "error"
                job.error = reason
                job.finished_at = job.finished_at or _utc_now()
                job.updated_at = _utc_now()
                failed_job_ids.append(job.id)

                rows = (
                    db.query(PublishPlatformStatus)
                    .filter(PublishPlatformStatus.job_id == job.id)
                    .all()
                )
                for row in rows:
                    if row.status not in TERMINAL_PLATFORM_STATUSES:
                        row.status = "error"
                        row.error = reason
                        row.updated_at = _utc_now()

                self._add_event(
                    db,
                    job.id,
                    "job_timeout",
                    reason,
                    {"max_age_seconds": max_age_seconds},
                )
            db.commit()
        finally:
            db.close()

        for task_id in failed_job_ids:
            task_state = self.ensure_memory_task(task_id)
            if not task_state:
                continue
            for platform, plat_status in task_state.platforms.items():
                if plat_status.status not in TERMINAL_PLATFORM_STATUSES:
                    plat_status.status = "error"
                    plat_status.error = reason
                    await self._notify_subscribers(
                        task_id,
                        {
                            "task_id": task_id,
                            "platform": platform,
                            "status": "error",
                            "progress": plat_status.progress,
                            "error": reason,
                        },
                    )
        return failed_job_ids

    def recover_interrupted_jobs(self, max_age_minutes: int) -> list[str]:
        cutoff = _utc_now() - timedelta(minutes=max(0, max_age_minutes))
        reason = "Job interrompido por reinício ou queda do processo."
        recovered_job_ids: list[str] = []
        db = SessionLocal()
        try:
            jobs = (
                db.query(PublishJob)
                .filter(PublishJob.status == "running")
                .filter(PublishJob.updated_at <= cutoff)
                .all()
            )
            for job in jobs:
                job.status = "error"
                job.error = reason
                job.finished_at = job.finished_at or _utc_now()
                job.updated_at = _utc_now()
                recovered_job_ids.append(job.id)

                rows = (
                    db.query(PublishPlatformStatus)
                    .filter(PublishPlatformStatus.job_id == job.id)
                    .all()
                )
                for row in rows:
                    if row.status not in TERMINAL_PLATFORM_STATUSES:
                        row.status = "error"
                        row.error = reason
                        row.updated_at = _utc_now()

                self._add_event(
                    db,
                    job.id,
                    "job_recovered_as_error",
                    reason,
                    {"max_age_minutes": max_age_minutes},
                )
            db.commit()
            return recovered_job_ids
        finally:
            db.close()

    def _add_event(
        self,
        db,
        task_id: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ):
        db.add(
            PublishJobEvent(
                job_id=task_id,
                type=event_type,
                message=message,
                payload_json=_dumps_json(payload or {}),
                created_at=_utc_now(),
            )
        )

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
            job.workspace_id = request.workspace_id
            job.status = status
            job.video_path = request.video_path
            job.thumb_path = request.thumb_path
            job.caption = request.caption
            job.accounts_json = _dumps_json(accounts)
            job.youtube_title = request.youtube_title
            job.youtube_tags_json = _dumps_json(youtube_tags or [])
            job.youtube_privacy = request.youtube_privacy
            job.instagram_format = request.instagram_format
            job.instagram_share_to_facebook = request.instagram_share_to_facebook
            job.instagram_fb_destination_id = request.instagram_fb_destination_id
            job.instagram_fb_destination_type = request.instagram_fb_destination_type
            job.scheduled_at = to_utc_naive(request.scheduled_at)
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

            self._add_event(
                db,
                task_id,
                "job_created",
                "Job criado.",
                {"mode": mode, "status": status},
            )
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
                if job.status == "canceled" and status != "canceled":
                    return
                if status == "uploading" and job.status == "queued":
                    job.status = "running"
                    job.started_at = job.started_at or now
                    self._add_event(
                        db,
                        task_id,
                        "job_running",
                        "Job marcado como em execução.",
                    )
                job.updated_at = now
                self._add_event(
                    db,
                    task_id,
                    "platform_status",
                    f"{platform}: {status}",
                    {
                        "platform": platform,
                        "status": status,
                        "progress": progress,
                        "error": error,
                    },
                )

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
                    self._add_event(
                        db,
                        task_id,
                        "job_error" if failed else "job_success",
                        job.error or "Job concluído com sucesso.",
                    )

            db.commit()
        finally:
            db.close()

    def _request_from_job(self, job: PublishJob) -> PublishRequest:
        return PublishRequest(
            mode=job.mode,
            workspace_id=job.workspace_id,
            scheduled_at=utc_naive_to_app_aware(job.scheduled_at),
            video_path=job.video_path,
            thumb_path=job.thumb_path,
            caption=job.caption,
            accounts=_loads_json(job.accounts_json, {}),
            youtube_title=job.youtube_title,
            youtube_tags=_loads_json(job.youtube_tags_json, []),
            youtube_privacy=job.youtube_privacy,
            instagram_format=job.instagram_format,
            instagram_share_to_facebook=bool(job.instagram_share_to_facebook),
            instagram_fb_destination_id=job.instagram_fb_destination_id,
            instagram_fb_destination_type=job.instagram_fb_destination_type,
        )

    def _job_to_dict(self, db, job: PublishJob) -> dict[str, Any]:
        platforms = (
            db.query(PublishPlatformStatus)
            .filter(PublishPlatformStatus.job_id == job.id)
            .order_by(PublishPlatformStatus.platform.asc())
            .all()
        )
        events = (
            db.query(PublishJobEvent)
            .filter(PublishJobEvent.job_id == job.id)
            .order_by(PublishJobEvent.created_at.asc())
            .all()
        )
        payload = {
            "id": job.id,
            "task_id": job.id,
            "workspace_id": job.workspace_id,
            "mode": job.mode,
            "status": job.status,
            "video_path": job.video_path,
            "thumb_path": job.thumb_path,
            "caption": job.caption,
            "accounts": _loads_json(job.accounts_json, {}),
            "youtube_title": job.youtube_title,
            "youtube_tags": _loads_json(job.youtube_tags_json, []),
            "youtube_privacy": job.youtube_privacy,
            "instagram_format": job.instagram_format,
            "instagram_share_to_facebook": bool(job.instagram_share_to_facebook),
            "instagram_fb_destination_id": job.instagram_fb_destination_id,
            "instagram_fb_destination_type": job.instagram_fb_destination_type,
            "scheduled_at": utc_naive_to_app_aware(job.scheduled_at),
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
            "events": [
                {
                    "id": event.id,
                    "job_id": event.job_id,
                    "type": event.type,
                    "message": event.message,
                    "payload": _loads_json(event.payload_json, {}),
                    "created_at": event.created_at,
                }
                for event in events
            ],
        }
        return _model_dump(PublishJobResponse(**payload))


task_manager = TaskManager()
