import asyncio
from typing import Dict, Any, List
from datetime import datetime

from app.models.schemas import TaskState, PlatformStatus

class TaskManager:
    """
    Gerencia o estado das tarefas (tasks) em memória.
    Também cuida do envio de eventos via SSE para monitoramento.
    """
    def __init__(self):
        # Armazena os estados das tarefas: {task_id: TaskState}
        self.tasks_state: Dict[str, TaskState] = {}
        # Lista de filas (asyncio.Queue) para cada task_id, para suportar múltiplos clientes SSE
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}

    def create_task(self, task_id: str, platforms: List[str]):
        """Inicializa uma task com as plataformas pendentes."""
        platform_statuses = {
            plat: PlatformStatus(platform=plat, status="pending")
            for plat in platforms
        }
        self.tasks_state[task_id] = TaskState(
            task_id=task_id,
            platforms=platform_statuses,
            created_at=datetime.utcnow()
        )
        self._subscribers[task_id] = []

    def get_task(self, task_id: str) -> TaskState | None:
        """Retorna o estado de uma task."""
        return self.tasks_state.get(task_id)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        """Inscreve-se para receber eventos de uma task (SSE)."""
        if task_id not in self._subscribers:
            self._subscribers[task_id] = []
        queue = asyncio.Queue()
        self._subscribers[task_id].append(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue):
        """Remove a inscrição de um cliente."""
        if task_id in self._subscribers:
            if queue in self._subscribers[task_id]:
                self._subscribers[task_id].remove(queue)
            # Limpeza caso não tenha mais inscritos (opcional)
            if not self._subscribers[task_id]:
                pass

    async def update_status(self, task_id: str, platform: str, status: str, progress: int = 0, error: str = None):
        """
        Atualiza o status de uma plataforma em uma task e notifica todos os clientes inscritos.
        """
        if task_id not in self.tasks_state:
            return

        # Atualiza o estado
        plat_status = self.tasks_state[task_id].platforms.get(platform)
        if not plat_status:
            return
            
        plat_status.status = status
        plat_status.progress = progress
        plat_status.error = error

        # Cria payload do evento
        event_payload = {
            "task_id": task_id,
            "platform": platform,
            "status": status,
            "progress": progress,
            "error": error
        }

        # Notifica inscritos via SSE
        if task_id in self._subscribers:
            for queue in self._subscribers[task_id]:
                await queue.put(event_payload)

# Instância global do gerenciador de tasks
task_manager = TaskManager()
