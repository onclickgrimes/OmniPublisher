import json
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import List

from app.models.schemas import PublishJobResponse
from app.services.task_manager import task_manager

router = APIRouter()


@router.get("/tasks", response_model=List[PublishJobResponse])
def list_tasks(status: str = None, limit: int = 100):
    """
    Lista publicações persistidas no SQLite.
    """
    return task_manager.list_jobs(status=status, limit=limit)


@router.get("/tasks/{task_id}", response_model=PublishJobResponse)
def get_task(task_id: str):
    """
    Retorna uma publicação persistida, incluindo status por plataforma.
    """
    job = task_manager.get_job(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Task ID não encontrado.")
    return job


@router.post("/tasks/{task_id}/cancel", response_model=PublishJobResponse)
async def cancel_task(task_id: str):
    """
    Cancela uma publicação queued/running.
    """
    canceled = await task_manager.cancel_job(task_id)
    if not canceled:
        job = task_manager.get_job(task_id)
        if not job:
            raise HTTPException(status_code=404, detail="Task ID não encontrado.")
        raise HTTPException(status_code=409, detail=f"Task já está em estado terminal: {job['status']}.")

    job = task_manager.get_job(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Task ID não encontrado.")
    return job


@router.get("/tasks/{task_id}/stream")
async def stream_task(task_id: str):
    """
    Endpoint Server-Sent Events (SSE) para monitorar o status do upload em tempo real.
    """
    # Verifica se a task existe
    task_state = task_manager.get_task(task_id)
    if not task_state:
        raise HTTPException(status_code=404, detail="Task ID não encontrado.")

    # Inscreve-se para escutar eventos dessa task
    queue = task_manager.subscribe(task_id)

    def all_done(platforms_state) -> bool:
        """Verifica se todas as plataformas terminaram (success ou error)"""
        return all(plat.status in ["success", "error", "canceled"] for plat in platforms_state.values())

    async def event_generator():
        # Envia o estado inicial assim que conecta
        initial_state = task_state.dict()
        # O Pydantic model dict pode conter datetime que não é serializável em json puro, vamos tratar:
        initial_state["created_at"] = initial_state["created_at"].isoformat()
        yield f"data: {json.dumps({'type': 'initial', 'state': initial_state})}\n\n"

        try:
            while True:
                # Se as plataformas já terminaram todas, avisa o client e sai (para tasks finalizadas antes de conectar)
                if all_done(task_state.platforms):
                    yield f"data: {json.dumps({'type': 'finished', 'task_id': task_id})}\n\n"
                    break

                try:
                    # Espera novo evento na fila por até 30 seg
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps({'type': 'update', 'event': event})}\n\n"
                    
                    # Atualiza task_state para checar a condição de término
                    # E checa se esse evento causou o fim de todas as plataformas
                    if all_done(task_state.platforms):
                        yield f"data: {json.dumps({'type': 'finished', 'task_id': task_id})}\n\n"
                        break

                except asyncio.TimeoutError:
                    # Mantém a conexão viva enviando heartbeats se não houver att
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        
        except asyncio.CancelledError:
            # Cliente desconectou abortando a requisição
            pass
        finally:
            # Desinscreve da fila independentemente do motivo de saída
            task_manager.unsubscribe(task_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
