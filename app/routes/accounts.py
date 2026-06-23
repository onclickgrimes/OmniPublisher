from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.models.db import Account, get_db
from app.models.schemas import AccountCreate, AccountResponse

router = APIRouter()

@router.post("/", response_model=AccountResponse)
def create_account(account: AccountCreate, db: Session = Depends(get_db)):
    """
    Cadastra uma nova conta na plataforma.
    O `settings_file` é gerado dinamicamente com base no ID da conta.
    """
    db_account = Account(
        platform=account.platform,
        name=account.name,
        identifier=account.identifier,
        credentials=account.credentials
    )
    db.add(db_account)
    db.commit()
    db.refresh(db_account)

    # Define o nome do arquivo de sessão baseado na plataforma e no ID único
    if db_account.platform == "youtube":
        db_account.settings_file = f"youtube_token_{db_account.id}.json"
    elif db_account.platform == "instagram":
        db_account.settings_file = f"instagram_settings_{db_account.id}.json"
    # TikTok usa o session_id direto do credentials e não salva arquivo

    db.commit()
    db.refresh(db_account)
    return db_account

@router.get("/", response_model=List[AccountResponse])
def list_accounts(platform: str = None, db: Session = Depends(get_db)):
    """
    Lista as contas cadastradas. Pode filtrar por plataforma.
    A senha (credentials) não é retornada por segurança (definido no AccountResponse).
    """
    query = db.query(Account)
    if platform:
        query = query.filter(Account.platform == platform)
    return query.all()
