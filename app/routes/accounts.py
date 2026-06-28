# pyrefly: ignore [missing-import]
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
from typing import List

from app.config import SESSIONS_DIR
from app.models.db import Account, AccountStatusCheck, Workspace, WorkspaceAccount, get_db
from app.models.schemas import (
    AccountChallengeSubmit,
    AccountCreate,
    GraphApiConnectResponse,
    InstagramFacebookDestinationResponse,
    AccountResponse,
    AccountStatusResponse,
    AccountUpdate,
    InstagramSessionSubmit,
)
from app.providers.instagram_api import _instagram_facebook_destination_status
from app.services.account_status_checker import account_status_checker
from app.services.session_manager import session_manager

router = APIRouter()


def _model_dump(model, **kwargs):
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    return model.dict(**kwargs)


def _get_account_or_404(account_id: str, db: Session) -> Account:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    return account


def _validate_credentials(platform: str, credentials: str | None):
    if platform in {"instagram", "tiktok"} and not str(credentials or "").strip():
        raise HTTPException(
            status_code=400,
            detail=f"Credenciais são obrigatórias para contas {platform}.",
        )


def _settings_file_for(account: Account) -> str | None:
    if account.platform == "youtube":
        return f"youtube_token_{account.id}.json"
    if account.platform == "instagram":
        return f"instagram_settings_{account.id}.json"
    return None


def _delete_settings_file(account: Account):
    if not account.settings_file:
        return
    settings_path = SESSIONS_DIR / account.settings_file
    try:
        if settings_path.exists() and settings_path.is_file():
            settings_path.unlink()
    except OSError:
        # Não bloqueia a remoção da conta por falha de limpeza de arquivo local.
        pass


@router.post("/", response_model=AccountResponse)
def create_account(account: AccountCreate, db: Session = Depends(get_db)):
    """
    Cadastra uma nova conta na plataforma.
    O `settings_file` é gerado dinamicamente com base no ID da conta.
    """
    _validate_credentials(account.platform, account.credentials)

    db_account = Account(
        platform=account.platform,
        name=account.name,
        identifier=account.identifier,
        credentials=account.credentials,
    )
    db.add(db_account)
    db.commit()
    db.refresh(db_account)

    db_account.settings_file = _settings_file_for(db_account)

    db.commit()
    db.refresh(db_account)
    return AccountResponse.from_account(db_account)


@router.get("/", response_model=List[AccountResponse])
def list_accounts(
    platform: str = None,
    workspace_id: str = None,
    db: Session = Depends(get_db),
):
    """
    Lista as contas cadastradas. Pode filtrar por plataforma e/ou workspace.
    A senha (credentials) não é retornada por segurança (definido no AccountResponse).
    """
    query = db.query(Account)
    if workspace_id:
        workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace não encontrado.")
        query = query.join(WorkspaceAccount, WorkspaceAccount.account_id == Account.id)
        query = query.filter(WorkspaceAccount.workspace_id == workspace_id)
    if platform:
        query = query.filter(Account.platform == platform)
    return [AccountResponse.from_account(a) for a in query.all()]


@router.get("/{account_id}", response_model=AccountResponse)
def get_account(account_id: str, db: Session = Depends(get_db)):
    """
    Retorna uma conta cadastrada sem expor credenciais.
    """
    return AccountResponse.from_account(_get_account_or_404(account_id, db))


@router.get("/{account_id}/status", response_model=AccountStatusResponse)
def get_account_status(account_id: str, refresh: bool = False):
    """
    Retorna o status de autenticação da conta, usando cache quando possível.
    """
    status = account_status_checker.check_account_status(account_id, refresh=refresh)
    if not status:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    return status


@router.post("/{account_id}/status/refresh", response_model=AccountStatusResponse)
def refresh_account_status(account_id: str, background_tasks: BackgroundTasks):
    """
    Marca a conta como checking e dispara uma nova verificação em background.
    """
    status = account_status_checker.mark_account_checking(account_id)
    if not status:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")

    background_tasks.add_task(
        account_status_checker.check_account_status,
        account_id,
        refresh=True,
    )
    return status


@router.post("/{account_id}/challenge", response_model=AccountStatusResponse)
def submit_account_challenge(account_id: str, payload: AccountChallengeSubmit):
    """
    Envia código de verificação solicitado pela plataforma.
    No momento, usado pelo Instagram quando o login entra em challenge.
    """
    try:
        status = account_status_checker.submit_instagram_challenge(account_id, payload.code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not status:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    return status


@router.post("/{account_id}/instagram/session", response_model=AccountStatusResponse)
def submit_instagram_session(account_id: str, payload: InstagramSessionSubmit):
    """
    Importa uma sessão web válida do Instagram usando o cookie sessionid.
    Útil quando o login por senha cai em checkpoint manual sem código.
    """
    try:
        status = account_status_checker.submit_instagram_sessionid(account_id, payload.sessionid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not status:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    return status


@router.get("/{account_id}/instagram/facebook-destination", response_model=InstagramFacebookDestinationResponse)
def get_instagram_facebook_destination(account_id: str, db: Session = Depends(get_db)):
    """
    Retorna a Página/Destino Facebook vinculada à conta Instagram, quando disponível.
    Usado pelo front para mostrar a opção de crosspost em Reels.
    """
    account = _get_account_or_404(account_id, db)
    if account.platform != "instagram":
        raise HTTPException(status_code=400, detail="A conta informada não é do Instagram.")

    try:
        client = session_manager.get_instagram_client(account.id)
        status = _instagram_facebook_destination_status(client)
    except Exception as exc:
        return {
            "account_id": account.id,
            "platform": "instagram",
            "available": False,
            "crosspost_supported": False,
            "requires_facebook_token": False,
            "share_to_fb_unavailable": None,
            "can_crosspost_without_fb_token": None,
            "destination_id": None,
            "destination_type": None,
            "destination_name": None,
            "source": None,
            "message": f"Não foi possível verificar a Página vinculada: {exc}",
        }

    return {
        "account_id": account.id,
        "platform": "instagram",
        **{key: value for key, value in status.items() if key != "diagnostics"},
    }


@router.patch("/{account_id}", response_model=AccountResponse)
def update_account(account_id: str, updates: AccountUpdate, db: Session = Depends(get_db)):
    """
    Atualiza dados editáveis de uma conta cadastrada.
    """
    db_account = _get_account_or_404(account_id, db)
    payload = _model_dump(updates, exclude_unset=True)

    next_credentials = payload.get("credentials", db_account.credentials)
    _validate_credentials(db_account.platform, next_credentials)

    for field in ["name", "identifier", "credentials"]:
        if field in payload:
            setattr(db_account, field, payload[field])

    if db_account.platform == "instagram":
        session_manager.clear_instagram_client(db_account.id)

    db.commit()
    db.refresh(db_account)
    return AccountResponse.from_account(db_account)


@router.delete("/{account_id}/graph-api", response_model=GraphApiConnectResponse)
def disconnect_graph_api(account_id: str, db: Session = Depends(get_db)):
    """
    Desconecta a Graph API de uma conta Instagram sem remover a conta.
    Remove tokens OAuth e dados da Página Facebook, mantendo credenciais instagrapi.
    """
    db_account = _get_account_or_404(account_id, db)
    if db_account.platform != "instagram":
        raise HTTPException(status_code=400, detail="A Graph API só se aplica a contas Instagram.")

    if not db_account.graph_token and not db_account.ig_business_id:
        raise HTTPException(status_code=400, detail="Esta conta não possui conexão com a Graph API.")

    db_account.graph_token = None
    db_account.graph_token_expires_at = None
    db_account.ig_business_id = None
    db_account.fb_page_id = None
    db_account.fb_page_token = None
    db_account.fb_page_name = None
    # account_type é mantido — o tipo da conta não muda por desconectar a API

    db.commit()
    db.refresh(db_account)
    return GraphApiConnectResponse(
        account_id=db_account.id,
        graph_connected=False,
        account_type=db_account.account_type,
        ig_business_id=None,
        fb_page_id=None,
        fb_page_name=None,
        graph_token_expires_at=None,
        message="Graph API desconectada. A conta continua ativa via instagrapi.",
    )


@router.delete("/{account_id}")
def delete_account(account_id: str, db: Session = Depends(get_db)):
    """
    Remove uma conta cadastrada e tenta limpar o arquivo de sessão associado.
    """
    db_account = _get_account_or_404(account_id, db)
    if db_account.platform == "instagram":
        session_manager.clear_instagram_client(db_account.id)
    _delete_settings_file(db_account)
    db.query(WorkspaceAccount).filter(WorkspaceAccount.account_id == account_id).delete()
    db.query(AccountStatusCheck).filter(AccountStatusCheck.account_id == account_id).delete()
    db.delete(db_account)
    db.commit()
    return {
        "success": True,
        "deletedAccountId": account_id,
    }
