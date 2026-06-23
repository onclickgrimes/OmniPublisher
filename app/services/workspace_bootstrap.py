from datetime import datetime

from app.models.db import Account, SessionLocal, Workspace, WorkspaceAccount


DEFAULT_WORKSPACE_NAME = "Default"
DEFAULT_WORKSPACE_SLUG = "default"


def ensure_default_workspace() -> str | None:
    """
    Cria um workspace padrão no primeiro boot de bancos antigos/novos.
    Se existirem contas globais e nenhum workspace, vincula todas ao Default.
    """
    db = SessionLocal()
    try:
        if db.query(Workspace).first():
            return None

        now = datetime.utcnow()
        workspace = Workspace(
            name=DEFAULT_WORKSPACE_NAME,
            slug=DEFAULT_WORKSPACE_SLUG,
            description="Workspace padrão criado automaticamente.",
            created_at=now,
            updated_at=now,
        )
        db.add(workspace)
        db.flush()

        accounts = db.query(Account).order_by(Account.platform.asc(), Account.name.asc()).all()
        for account in accounts:
            db.add(
                WorkspaceAccount(
                    workspace_id=workspace.id,
                    account_id=account.id,
                    label=account.name,
                    is_default=False,
                    created_at=now,
                )
            )

        db.commit()
        return workspace.id
    finally:
        db.close()
