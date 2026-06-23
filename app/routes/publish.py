import os
from uuid import uuid4
from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.models.schemas import PublishRequest, PublishResponse
from app.services.task_manager import task_manager
from app.services.orchestrator import orchestrator

router = APIRouter()

@router.post("/publish/omnichannel", response_model=PublishResponse)
async def publish_omnichannel(request: PublishRequest, background_tasks: BackgroundTasks):
    """
    Recebe as informações do vídeo e dispara as publicações assincronamente.
    """
    if not os.path.exists(request.video_path):
        raise HTTPException(status_code=400, detail=f"O arquivo de vídeo não foi encontrado no caminho: {request.video_path}")
    
    if not request.accounts:
        raise HTTPException(status_code=400, detail="Pelo menos uma conta deve ser informada no mapeamento 'accounts'.")

    task_id = str(uuid4())

    # Registra a task no gerenciador de estados, criando chaves por plataforma
    platforms = list(request.accounts.keys())
    task_manager.create_task(task_id, platforms)

    # Adiciona a função execute do orquestrador no background
    background_tasks.add_task(orchestrator.execute, task_id, request)

    return PublishResponse(
        task_id=task_id, 
        status="accepted", 
        message="Upload iniciado. Acompanhe pelo endpoint SSE."
    )
