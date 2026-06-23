import os
from uuid import uuid4
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.db import Account, get_db
from app.models.schemas import PublishRequest, PublishResponse
from app.services.task_manager import task_manager
from app.services.orchestrator import orchestrator

router = APIRouter()

SUPPORTED_PLATFORMS = {"youtube", "instagram", "tiktok"}


def _to_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _validate_publish_request(request: PublishRequest, db: Session):
    if request.mode == "scheduled" and request.scheduled_at is None:
        raise HTTPException(
            status_code=400,
            detail="scheduled_at é obrigatório quando mode='scheduled'.",
        )

    if not os.path.isfile(request.video_path):
        raise HTTPException(
            status_code=400,
            detail=f"O arquivo de vídeo não foi encontrado no caminho: {request.video_path}",
        )

    if request.thumb_path is not None:
        request.thumb_path = request.thumb_path.strip() or None
        if request.thumb_path and not os.path.isfile(request.thumb_path):
            raise HTTPException(
                status_code=400,
                detail=f"O arquivo de thumbnail não foi encontrado no caminho: {request.thumb_path}",
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
    task_manager.create_task(
        task_id,
        platforms,
        request=request,
        mode=request.mode,
        status="queued",
    )

    if request.mode == "immediate":
        background_tasks.add_task(orchestrator.execute, task_id, request)
        message = "Upload iniciado. Acompanhe pelo endpoint SSE."
        response_status = "accepted"
    else:
        message = "Upload agendado. O scheduler interno disparará a publicação no horário configurado."
        response_status = "queued"

    return PublishResponse(
        task_id=task_id, 
        status=response_status,
        message=message,
        mode=request.mode,
        scheduled_at=_to_utc_naive(request.scheduled_at),
    )
