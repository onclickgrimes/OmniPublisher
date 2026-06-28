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

    Contas Instagram Business/Creator podem ter autenticação dual:
    - Instagrapi: via credentials (senha) + settings_file (sessão)
    - Graph API:  via graph_token (OAuth) + ig_business_id + fb_page_token
    Ambas as engines coexistem no mesmo registro sem duplicar a conta.
    """
    __tablename__ = "accounts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    platform = Column(String, index=True, nullable=False) # "youtube", "instagram", "tiktok"
    name = Column(String, nullable=False)                 # Nome para exibição (ex: "Loja Principal")
    identifier = Column(String, nullable=False)           # Username (Insta), Email (YT), ou Handle (TikTok)
    credentials = Column(Text, nullable=True)             # Senha (Insta), SessionID (TikTok), null para YouTube (usa token)
    settings_file = Column(String, nullable=True)         # Nome do arquivo de sessão (ex: instagram_settings_ID.json)

    # --- Meta Graph API (dual-auth para Instagram Business/Creator) ---
    account_type = Column(String, nullable=True)            # "personal" | "business" | "creator" (None = desconhecido)
    graph_token = Column(Text, nullable=True)               # User Access Token da Graph API
    graph_token_expires_at = Column(DateTime, nullable=True)  # Expiração do token OAuth
    ig_business_id = Column(String, nullable=True)          # Instagram Business Account ID (para Graph API)
    fb_page_id = Column(String, nullable=True)              # Facebook Page ID vinculada
    fb_page_token = Column(Text, nullable=True)             # Page Access Token (não expira se long-lived)
    fb_page_name = Column(String, nullable=True)            # Nome da Página para exibição


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


class IntegrationConfig(Base):
    """
    Configura credenciais de integrações externas usadas pelo sidecar.
    Segredos ficam persistidos no banco local e não são lidos de variáveis de ambiente.
    """
    __tablename__ = "integration_configs"
    __table_args__ = (UniqueConstraint("provider", name="uq_integration_configs_provider"),)

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    provider = Column(String, nullable=False, index=True)
    facebook_app_id = Column(String, nullable=True)
    facebook_app_secret = Column(Text, nullable=True)
    facebook_login_config_id = Column(String, nullable=True)
    instagram_app_id = Column(String, nullable=True)
    instagram_app_secret = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


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

        # --- Graph API dual-auth columns on accounts ---
        account_columns = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(accounts)").fetchall()
        }
        if "account_type" not in account_columns:
            conn.exec_driver_sql("ALTER TABLE accounts ADD COLUMN account_type VARCHAR")
        if "graph_token" not in account_columns:
            conn.exec_driver_sql("ALTER TABLE accounts ADD COLUMN graph_token TEXT")
        if "graph_token_expires_at" not in account_columns:
            conn.exec_driver_sql("ALTER TABLE accounts ADD COLUMN graph_token_expires_at DATETIME")
        if "ig_business_id" not in account_columns:
            conn.exec_driver_sql("ALTER TABLE accounts ADD COLUMN ig_business_id VARCHAR")
        if "fb_page_id" not in account_columns:
            conn.exec_driver_sql("ALTER TABLE accounts ADD COLUMN fb_page_id VARCHAR")
        if "fb_page_token" not in account_columns:
            conn.exec_driver_sql("ALTER TABLE accounts ADD COLUMN fb_page_token TEXT")
        if "fb_page_name" not in account_columns:
            conn.exec_driver_sql("ALTER TABLE accounts ADD COLUMN fb_page_name VARCHAR")

        integration_tables = {
            row[0] for row in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "integration_configs" in integration_tables:
            integration_columns = {
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(integration_configs)").fetchall()
            }
            if "facebook_app_id" not in integration_columns:
                conn.exec_driver_sql("ALTER TABLE integration_configs ADD COLUMN facebook_app_id VARCHAR")
            if "facebook_app_secret" not in integration_columns:
                conn.exec_driver_sql("ALTER TABLE integration_configs ADD COLUMN facebook_app_secret TEXT")
            if "facebook_login_config_id" not in integration_columns:
                conn.exec_driver_sql("ALTER TABLE integration_configs ADD COLUMN facebook_login_config_id VARCHAR")
            if "instagram_app_id" not in integration_columns:
                conn.exec_driver_sql("ALTER TABLE integration_configs ADD COLUMN instagram_app_id VARCHAR")
            if "instagram_app_secret" not in integration_columns:
                conn.exec_driver_sql("ALTER TABLE integration_configs ADD COLUMN instagram_app_secret TEXT")

            integration_columns = {
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(integration_configs)").fetchall()
            }
            if "app_id" in integration_columns or "app_secret" in integration_columns:
                conn.exec_driver_sql(
                    """
                    CREATE TABLE integration_configs_new (
                        id VARCHAR(36) NOT NULL,
                        provider VARCHAR NOT NULL,
                        facebook_app_id VARCHAR,
                        facebook_app_secret TEXT,
                        facebook_login_config_id VARCHAR,
                        instagram_app_id VARCHAR,
                        instagram_app_secret TEXT,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        PRIMARY KEY (id),
                        UNIQUE (provider)
                    )
                    """
                )
                conn.exec_driver_sql(
                    """
                    INSERT INTO integration_configs_new (
                        id,
                        provider,
                        facebook_app_id,
                        facebook_app_secret,
                        facebook_login_config_id,
                        instagram_app_id,
                        instagram_app_secret,
                        created_at,
                        updated_at
                    )
                    SELECT
                        id,
                        provider,
                        facebook_app_id,
                        facebook_app_secret,
                        facebook_login_config_id,
                        instagram_app_id,
                        instagram_app_secret,
                        created_at,
                        updated_at
                    FROM integration_configs
                    """
                )
                conn.exec_driver_sql("DROP TABLE integration_configs")
                conn.exec_driver_sql("ALTER TABLE integration_configs_new RENAME TO integration_configs")
                conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_integration_configs_provider ON integration_configs (provider)"
                )
