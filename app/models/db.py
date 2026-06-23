import uuid
# pyrefly: ignore [missing-import]
from sqlalchemy import create_engine, Column, String, Text
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import declarative_base, sessionmaker

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

# Dependência para injetar a sessão do DB nas rotas do FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
