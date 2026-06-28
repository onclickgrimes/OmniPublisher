import os
from uuid import uuid4
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.db import Account, Workspace, WorkspaceAccount, get_db
from app.models.schemas import PublishRequest, PublishResponse
from app.services.task_manager import task_manager
from app.services.orchestrator import orchestrator
from app.services.time_utils import to_utc_naive, utc_naive_to_app_aware

router = APIRouter()

SUPPORTED_PLATFORMS = {"youtube", "instagram", "tiktok", "facebook"}


def _to_utc_naive(value: datetime | None) -> datetime | None:
    return to_utc_naive(value)


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

    if request.workspace_id is not None:
        request.workspace_id = request.workspace_id.strip() or None

    if request.instagram_fb_destination_id is not None:
        request.instagram_fb_destination_id = request.instagram_fb_destination_id.strip() or None

    if request.workspace_id:
        workspace = db.query(Workspace).filter(Workspace.id == request.workspace_id).first()
        if not workspace:
            raise HTTPException(
                status_code=400,
                detail=f"Workspace '{request.workspace_id}' não encontrado.",
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

        if request.workspace_id:
            linked = (
                db.query(WorkspaceAccount)
                .filter(WorkspaceAccount.workspace_id == request.workspace_id)
                .filter(WorkspaceAccount.account_id == account_id)
                .first()
            )
            if not linked:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Conta '{account_id}' não está vinculada ao workspace "
                        f"'{request.workspace_id}'."
                    ),
                )

        normalized_accounts[platform] = account_id

    request.accounts = normalized_accounts

    if request.instagram_fb_destination_type and not request.instagram_fb_destination_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "instagram_fb_destination_type só pode ser informado junto de "
                "instagram_fb_destination_id."
            ),
        )

    if request.instagram_fb_destination_id and not request.instagram_share_to_facebook:
        raise HTTPException(
            status_code=400,
            detail=(
                "instagram_share_to_facebook=true é obrigatório quando "
                "instagram_fb_destination_id é informado."
            ),
        )

    if request.instagram_fb_destination_id and request.instagram_fb_destination_type is None:
        request.instagram_fb_destination_type = "PAGE"

    if request.instagram_share_to_facebook:
        if "instagram" not in request.accounts:
            raise HTTPException(
                status_code=400,
                detail="instagram_share_to_facebook requer uma conta Instagram em accounts.",
            )
        if request.instagram_format != "reels":
            raise HTTPException(
                status_code=400,
                detail="Crosspost do Instagram para Facebook está disponível apenas para Reels.",
            )

        # Inteligência Dual-Auth: Se a conta Instagram selecionada tiver token do Facebook,
        # separamos a publicação em duas tarefas independentes em vez de usar o crosspost interno.
        ig_account_id = request.accounts["instagram"]
        ig_account = db.query(Account).filter(Account.id == ig_account_id).first()
        if ig_account and getattr(ig_account, "fb_page_token", None):
            request.accounts["facebook"] = ig_account_id
            request.instagram_share_to_facebook = False # Desativa o crosspost acoplado
            request.instagram_fb_destination_id = None
            request.instagram_fb_destination_type = None

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
        workspace_id=request.workspace_id,
        mode=request.mode,
        scheduled_at=utc_naive_to_app_aware(_to_utc_naive(request.scheduled_at)),
    )
