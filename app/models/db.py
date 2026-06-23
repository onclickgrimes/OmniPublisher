import uuid
# pyrefly: ignore [missing-import]
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, create_engine
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


class PublishJob(Base):
    """
    Representa uma publicação imediata ou agendada.
    """
    __tablename__ = "publish_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    mode = Column(String, index=True, nullable=False, default="immediate")
    status = Column(String, index=True, nullable=False, default="queued")
    video_path = Column(Text, nullable=False)
    caption = Column(Text, nullable=False)
    accounts_json = Column(Text, nullable=False)
    youtube_title = Column(Text, nullable=True)
    youtube_tags_json = Column(Text, nullable=True)
    youtube_privacy = Column(String, nullable=False, default="public")
    instagram_format = Column(String, nullable=False, default="reels")
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
