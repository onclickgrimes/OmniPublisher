from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.config import SESSIONS_DIR
from app.models.db import Account, AccountStatusCheck, Workspace, WorkspaceAccount, get_db
from app.models.schemas import AccountCreate, AccountResponse, AccountStatusResponse, AccountUpdate
from app.services.account_status_checker import account_status_checker

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
    return db_account


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
    return query.all()


@router.get("/{account_id}", response_model=AccountResponse)
def get_account(account_id: str, db: Session = Depends(get_db)):
    """
    Retorna uma conta cadastrada sem expor credenciais.
    """
    return _get_account_or_404(account_id, db)


@router.get("/{account_id}/status", response_model=AccountStatusResponse)
def get_account_status(account_id: str, refresh: bool = False):
    """
    Retorna o status de autenticação da conta, usando cache quando possível.
    """
    status = account_status_checker.check_account_status(account_id, refresh=refresh)
    if not status:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    return status


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

    db.commit()
    db.refresh(db_account)
    return db_account


@router.delete("/{account_id}")
def delete_account(account_id: str, db: Session = Depends(get_db)):
    """
    Remove uma conta cadastrada e tenta limpar o arquivo de sessão associado.
    """
    db_account = _get_account_or_404(account_id, db)
    _delete_settings_file(db_account)
    db.query(WorkspaceAccount).filter(WorkspaceAccount.account_id == account_id).delete()
    db.query(AccountStatusCheck).filter(AccountStatusCheck.account_id == account_id).delete()
    db.delete(db_account)
    db.commit()
    return {
        "success": True,
        "deletedAccountId": account_id,
    }
