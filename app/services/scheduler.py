import asyncio

from app.config import SCHEDULER_INTERVAL_SECONDS
from app.services.orchestrator import orchestrator
from app.services.task_manager import task_manager


class PublishScheduler:
    """
    Worker interno que varre o SQLite e dispara posts agendados vencidos.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self):
        if self._task and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="omnipublisher-scheduler")

    async def stop(self):
        self._stopping.set()
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self):
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                print(f"[scheduler] Falha ao varrer posts agendados: {exc}")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=max(1, SCHEDULER_INTERVAL_SECONDS),
                )
            except asyncio.TimeoutError:
                pass

    async def run_once(self):
        for task_id in task_manager.claim_due_jobs():
            request = task_manager.get_publish_request(task_id)
            if not request:
                task_manager.fail_job(task_id, "Job agendado não encontrado ao disparar.")
                continue

            task_manager.ensure_memory_task(task_id)
            asyncio.create_task(
                orchestrator.execute(task_id, request),
                name=f"omnipublisher-job-{task_id}",
            )


scheduler = PublishScheduler()
