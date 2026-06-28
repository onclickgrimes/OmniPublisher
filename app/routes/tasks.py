import json
import asyncio
from uuid import uuid4

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
# pyrefly: ignore [missing-import]
from fastapi.responses import StreamingResponse
from typing import List
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

from app.models.db import get_db
from app.models.schemas import PublishJobResponse, PublishRequest, PublishResponse, RetryPlatformResponse
from app.routes.publish import SUPPORTED_PLATFORMS, _validate_publish_request
from app.services.orchestrator import orchestrator
from app.services.task_manager import task_manager

router = APIRouter()


@router.get("/tasks", response_model=List[PublishJobResponse])
def list_tasks(status: str = None, workspace_id: str = None, limit: int = 100):
    """
    Lista publicações persistidas no SQLite.
    """
    return task_manager.list_jobs(status=status, workspace_id=workspace_id, limit=limit)


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


@router.post("/tasks/{task_id}/platforms/{platform}/retry", response_model=RetryPlatformResponse)
async def retry_task_platform(
    task_id: str,
    platform: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Cria um novo job imediato para reenviar apenas uma plataforma que falhou/cancelou.
    """
    normalized_platform = str(platform or "").strip().lower()
    if normalized_platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Plataforma '{platform}' não suportada.",
        )

    job = task_manager.get_job(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Task ID não encontrado.")

    platform_statuses = job.get("platforms") or []
    platform_status = next(
        (
            item for item in platform_statuses
            if str(item.get("platform") or "").strip().lower() == normalized_platform
        ),
        None,
    )
    if not platform_status:
        raise HTTPException(
            status_code=400,
            detail=f"A plataforma '{normalized_platform}' não existe na task '{task_id}'.",
        )

    current_status = str(platform_status.get("status") or "").strip().lower()
    if current_status not in {"error", "canceled"}:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Retry permitido apenas para plataforma com erro ou cancelada. "
                f"Status atual de '{normalized_platform}': {current_status or 'desconhecido'}."
            ),
        )

    accounts = job.get("accounts") or {}
    account_id = str(accounts.get(normalized_platform) or platform_status.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(
            status_code=400,
            detail=f"Não foi possível identificar a conta usada em '{normalized_platform}'.",
        )

    retry_request = PublishRequest(
        workspace_id=job.get("workspace_id"),
        mode="immediate",
        scheduled_at=None,
        video_path=job.get("video_path"),
        thumb_path=job.get("thumb_path"),
        caption=job.get("caption"),
        accounts={normalized_platform: account_id},
        youtube_title=job.get("youtube_title"),
        youtube_tags=job.get("youtube_tags") or [],
        youtube_privacy=job.get("youtube_privacy") or "public",
        instagram_format=job.get("instagram_format") or "reels",
        instagram_share_to_facebook=False,
        instagram_fb_destination_id=None,
        instagram_fb_destination_type=None,
    )
    _validate_publish_request(retry_request, db)

    retry_task_id = str(uuid4())
    task_manager.create_task(
        retry_task_id,
        [normalized_platform],
        request=retry_request,
        mode=retry_request.mode,
        status="queued",
    )
    background_tasks.add_task(orchestrator.execute, retry_task_id, retry_request)

    message = f"Retry iniciado para {normalized_platform}. Acompanhe pelo endpoint SSE."
    retry_response = PublishResponse(
        task_id=retry_task_id,
        status="accepted",
        message=message,
        workspace_id=retry_request.workspace_id,
        mode=retry_request.mode,
        scheduled_at=None,
    )
    retry_payload = (
        retry_response.model_dump()
        if hasattr(retry_response, "model_dump")
        else retry_response.dict()
    )
    return RetryPlatformResponse(
        source_task_id=task_id,
        platform=normalized_platform,
        retry_task=retry_response,
        **retry_payload,
    )


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
