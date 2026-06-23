# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, List
from datetime import datetime


PlatformName = Literal["youtube", "instagram", "tiktok"]

# --- Pydantic Models para Accounts ---

class AccountCreate(BaseModel):
    platform: PlatformName = Field(..., description="Plataforma da conta cadastrada.")
    name: str = Field(..., min_length=1, description="Nome de exibição (ex: Loja Oficial)")
    identifier: str = Field(..., min_length=1, description="Username, email ou handle da conta.")
    credentials: Optional[str] = Field(
        None,
        description=(
            "Credencial persistida da conta. Use senha para Instagram, cookie/session_id "
            "para TikTok e null para YouTube, que usa OAuth no primeiro upload."
        ),
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "platform": "tiktok",
                    "name": "TikTok Principal",
                    "identifier": "@minha_conta",
                    "credentials": "COOKIE_SESSIONID_DO_TIKTOK",
                },
                {
                    "platform": "youtube",
                    "name": "Canal Principal",
                    "identifier": "email@exemplo.com",
                    "credentials": None,
                },
            ]
        }


class AccountUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, description="Novo nome de exibição.")
    identifier: Optional[str] = Field(None, min_length=1, description="Novo username, email ou handle.")
    credentials: Optional[str] = Field(
        None,
        description="Nova credencial da conta. O campo só é alterado quando enviado no PATCH.",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "name": "TikTok Principal",
                "identifier": "@minha_conta",
                "credentials": "NOVO_COOKIE_SESSIONID_DO_TIKTOK",
            }
        }

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
    accounts: Dict[PlatformName, str] = Field(
        ...,
        description=(
            "Mapeamento de plataforma para o ID da conta previamente cadastrada via POST /accounts/. "
            "Nao envie credenciais aqui."
        ),
    )
    
    youtube_title: Optional[str] = Field(None, description="Título do vídeo no YouTube")
    youtube_tags: Optional[List[str]] = Field(None, description="Tags do vídeo no YouTube")
    youtube_privacy: Literal["public", "private", "unlisted"] = Field("public", description="Privacidade do vídeo no YouTube")
    instagram_format: Literal["reels", "feed"] = Field("reels", description="Formato do vídeo no Instagram")

    class Config:
        json_schema_extra = {
            "example": {
                "video_path": "C:/Projetos-NestJS/OmniPublisher/1-1.mp4",
                "caption": "Publicacao via OmniPublisher! #ola",
                "accounts": {
                    "tiktok": "a1f4f6fd-0974-4556-b88d-b2327a478170",
                },
                "youtube_title": "Titulo do video",
                "youtube_tags": ["automacao", "video"],
                "youtube_privacy": "public",
                "instagram_format": "reels",
            }
        }

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
