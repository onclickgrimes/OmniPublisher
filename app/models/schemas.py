# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, List
from datetime import datetime

# --- Pydantic Models para Accounts ---

class AccountCreate(BaseModel):
    platform: Literal["youtube", "instagram", "tiktok"]
    name: str = Field(..., description="Nome de exibição (ex: Loja Oficial)")
    identifier: str = Field(..., description="Username, Email ou Handle")
    credentials: Optional[str] = Field(None, description="Senha (Instagram) ou Cookie/SessionID (TikTok)")

class AccountResponse(BaseModel):
    id: str
    platform: str
    name: str
    identifier: str
    
    class Config:
        from_attributes = True

# --- Pydantic Models para Publicação ---

class PublishRequest(BaseModel):
    video_path: str = Field(..., description="Caminho absoluto do arquivo de vídeo")
    caption: str = Field(..., description="Legenda do vídeo")
    
    # Agora recebemos um mapeamento de plataforma para ID da conta
    # Ex: {"youtube": "uuid-1234", "instagram": "uuid-5678", "tiktok": "uuid-9012"}
    accounts: Dict[str, str] = Field(..., description="Mapeamento de plataformas para o ID da conta a ser utilizada")
    
    youtube_title: Optional[str] = Field(None, description="Título do vídeo no YouTube")
    youtube_tags: Optional[List[str]] = Field(None, description="Tags do vídeo no YouTube")
    youtube_privacy: Literal["public", "private", "unlisted"] = Field("public", description="Privacidade do vídeo no YouTube")
    instagram_format: Literal["reels", "feed"] = Field("reels", description="Formato do vídeo no Instagram")

class PlatformStatus(BaseModel):
    platform: str
    status: Literal["pending", "uploading", "success", "error"]
    progress: int = 0
    error: Optional[str] = None

class TaskState(BaseModel):
    task_id: str
    platforms: Dict[str, PlatformStatus]
    created_at: datetime = Field(default_factory=datetime.utcnow)

class PublishResponse(BaseModel):
    task_id: str
    status: str
    message: str
