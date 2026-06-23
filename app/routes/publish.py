import os
from uuid import uuid4
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.db import Account, get_db
from app.models.schemas import PublishRequest, PublishResponse
from app.services.task_manager import task_manager
from app.services.orchestrator import orchestrator

router = APIRouter()

SUPPORTED_PLATFORMS = {"youtube", "instagram", "tiktok"}


def _validate_publish_request(request: PublishRequest, db: Session):
    if not os.path.isfile(request.video_path):
        raise HTTPException(
            status_code=400,
            detail=f"O arquivo de vídeo não foi encontrado no caminho: {request.video_path}",
        )

    if not request.accounts:
        raise HTTPException(
            status_code=400,
            detail="Pelo menos uma conta deve ser informada no mapeamento 'accounts'.",
        )

    normalized_accounts: dict[str, str] = {}
    for raw_platform, raw_account_id in request.accounts.items():
        platform = str(raw_platform or "").strip().lower()
        account_id = str(raw_account_id or "").strip()

        if platform not in SUPPORTED_PLATFORMS:
            raise HTTPException(
                status_code=400,
                detail=f"Plataforma '{raw_platform}' não suportada.",
            )

        if not account_id:
            raise HTTPException(
                status_code=400,
                detail=f"ID da conta obrigatório para a plataforma '{platform}'.",
            )

        if platform in normalized_accounts:
            raise HTTPException(
                status_code=400,
                detail=f"Plataforma duplicada no mapeamento 'accounts': {platform}.",
            )

        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise HTTPException(
                status_code=400,
                detail=f"Conta '{account_id}' não encontrada para a plataforma '{platform}'.",
            )

        if account.platform != platform:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Conta '{account_id}' pertence à plataforma '{account.platform}', "
                    f"mas foi enviada para '{platform}'."
                ),
            )

        normalized_accounts[platform] = account_id

    request.accounts = normalized_accounts

@router.post("/publish/omnichannel", response_model=PublishResponse)
async def publish_omnichannel(
    request: PublishRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Recebe as informações do vídeo e dispara as publicações assincronamente.
    """
    _validate_publish_request(request, db)

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
