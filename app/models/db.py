import uuid
# pyrefly: ignore [missing-import]
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

from app.config import SQLALCHEMY_DATABASE_URL

# Para SQLite, 'check_same_thread': False é necessário ao usar com FastAPI
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Account(Base):
    """
    Representa uma conta de plataforma salva no sistema.
    As credenciais (senhas ou session_ids) são salvas em texto plano conforme solicitado.
    """
    __tablename__ = "accounts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    platform = Column(String, index=True, nullable=False) # "youtube", "instagram", "tiktok"
    name = Column(String, nullable=False)                 # Nome para exibição (ex: "Loja Principal")
    identifier = Column(String, nullable=False)           # Username (Insta), Email (YT), ou Handle (TikTok)
    credentials = Column(Text, nullable=True)             # Senha (Insta), SessionID (TikTok), null para YouTube (usa token)
    settings_file = Column(String, nullable=True)         # Nome do arquivo de sessão (ex: instagram_settings_ID.json)


class Workspace(Base):
    """
    Agrupa contas e publicações por contexto de trabalho/projeto.
    """
    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("slug", name="uq_workspaces_slug"),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WorkspaceAccount(Base):
    """
    Associação N:N entre workspaces e contas.
    """
    __tablename__ = "workspace_accounts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "account_id", name="uq_workspace_accounts_pair"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    workspace_id = Column(String(36), ForeignKey("workspaces.id"), index=True, nullable=False)
    account_id = Column(String(36), ForeignKey("accounts.id"), index=True, nullable=False)
    label = Column(String, nullable=True)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class AccountStatusCheck(Base):
    """
    Cache histórico de verificações de autenticação das contas.
    """
    __tablename__ = "account_status_checks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    account_id = Column(String(36), ForeignKey("accounts.id"), index=True, nullable=False)
    status = Column(String, index=True, nullable=False)
    message = Column(Text, nullable=True)
    checked_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    raw_json = Column(Text, nullable=True)


class PublishJob(Base):
    """
    Representa uma publicação imediata ou agendada.
    """
    __tablename__ = "publish_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    workspace_id = Column(String(36), index=True, nullable=True)
    mode = Column(String, index=True, nullable=False, default="immediate")
    status = Column(String, index=True, nullable=False, default="queued")
    video_path = Column(Text, nullable=False)
    thumb_path = Column(Text, nullable=True)
    caption = Column(Text, nullable=False)
    accounts_json = Column(Text, nullable=False)
    youtube_title = Column(Text, nullable=True)
    youtube_tags_json = Column(Text, nullable=True)
    youtube_privacy = Column(String, nullable=False, default="public")
    instagram_format = Column(String, nullable=False, default="reels")
    instagram_share_to_facebook = Column(Boolean, nullable=False, default=False)
    instagram_fb_destination_id = Column(Text, nullable=True)
    instagram_fb_destination_type = Column(String, nullable=True)
    scheduled_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)


class PublishPlatformStatus(Base):
    """
    Status persistido por plataforma dentro de um job.
    """
    __tablename__ = "publish_platform_statuses"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    job_id = Column(String(36), ForeignKey("publish_jobs.id"), index=True, nullable=False)
    platform = Column(String, index=True, nullable=False)
    account_id = Column(String(36), nullable=False)
    status = Column(String, nullable=False, default="pending")
    progress = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PublishJobEvent(Base):
    """
    Registro append-only de eventos relevantes de um job.
    """
    __tablename__ = "publish_job_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    job_id = Column(String(36), ForeignKey("publish_jobs.id"), index=True, nullable=False)
    type = Column(String, index=True, nullable=False)
    message = Column(Text, nullable=False)
    payload_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

# Dependência para injetar a sessão do DB nas rotas do FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_database_schema():
    """
    Aplica pequenas migrações compatíveis com SQLite para bancos de dev existentes.
    """
    with engine.begin() as conn:
        publish_job_columns = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(publish_jobs)").fetchall()
        }
        if "thumb_path" not in publish_job_columns:
            conn.exec_driver_sql("ALTER TABLE publish_jobs ADD COLUMN thumb_path TEXT")
        if "workspace_id" not in publish_job_columns:
            conn.exec_driver_sql("ALTER TABLE publish_jobs ADD COLUMN workspace_id VARCHAR(36)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_publish_jobs_workspace_id ON publish_jobs (workspace_id)")
        if "instagram_share_to_facebook" not in publish_job_columns:
            conn.exec_driver_sql(
                "ALTER TABLE publish_jobs ADD COLUMN instagram_share_to_facebook BOOLEAN NOT NULL DEFAULT 0"
            )
        if "instagram_fb_destination_id" not in publish_job_columns:
            conn.exec_driver_sql("ALTER TABLE publish_jobs ADD COLUMN instagram_fb_destination_id TEXT")
        if "instagram_fb_destination_type" not in publish_job_columns:
            conn.exec_driver_sql("ALTER TABLE publish_jobs ADD COLUMN instagram_fb_destination_type VARCHAR")
