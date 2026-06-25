# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, List
from datetime import datetime


PlatformName = Literal["youtube", "instagram", "tiktok"]
PublishMode = Literal["immediate", "scheduled"]
JobStatus = Literal["queued", "running", "success", "error", "canceled"]
AccountConnectionStatus = Literal["connected", "disconnected", "needs_auth", "checking", "error", "unknown"]

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
                {
                    "platform": "instagram",
                    "name": "Instagram Principal",
                    "identifier": "usuario_instagram",
                    "credentials": "SENHA_DO_INSTAGRAM",
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


class AccountStatusResponse(BaseModel):
    account_id: str
    platform: str
    name: str
    identifier: str
    status: AccountConnectionStatus
    message: Optional[str] = None
    checked_at: datetime
    expires_at: datetime
    cached: bool = False

    class Config:
        json_schema_extra = {
            "example": {
                "account_id": "account-id-tiktok",
                "platform": "tiktok",
                "name": "TikTok Principal",
                "identifier": "@minha_conta",
                "status": "connected",
                "message": "Session ID do TikTok cadastrado. A validação web completa ocorre no publish.",
                "checked_at": "2026-06-24T00:16:54.389039",
                "expires_at": "2026-06-24T00:26:54.389039",
                "cached": True,
            }
        }


# --- Pydantic Models para Workspaces ---

class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, description="Nome do workspace.")
    slug: Optional[str] = Field(None, min_length=1, description="Identificador legível opcional.")
    description: Optional[str] = Field(None, description="Descrição opcional do workspace.")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Histórias da Bíblia",
                "slug": "historias-da-biblia",
                "description": "Workspace de conteúdo bíblico.",
            }
        }


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, description="Novo nome do workspace.")
    slug: Optional[str] = Field(None, min_length=1, description="Novo identificador legível.")
    description: Optional[str] = Field(None, description="Nova descrição opcional.")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Marketing Pessoal",
                "slug": "marketing-pessoal",
                "description": "Workspace para conteúdo de marketing pessoal.",
            }
        }


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkspaceAccountAttach(BaseModel):
    account_id: str = Field(..., description="ID da conta global a vincular ao workspace.")
    label: Optional[str] = Field(None, description="Rótulo opcional da conta neste workspace.")
    is_default: bool = Field(False, description="Marca a conta como padrão dentro do workspace.")

    class Config:
        json_schema_extra = {
            "example": {
                "account_id": "account-id-tiktok",
                "label": "TikTok Principal",
                "is_default": True,
            }
        }


class WorkspaceAccountResponse(BaseModel):
    id: str
    workspace_id: str
    account_id: str
    platform: str
    name: str
    identifier: str
    label: Optional[str] = None
    is_default: bool
    created_at: datetime

    class Config:
        json_schema_extra = {
            "example": {
                "id": "workspace-account-link-id",
                "workspace_id": "workspace-id-default",
                "account_id": "account-id-tiktok",
                "platform": "tiktok",
                "name": "TikTok Principal",
                "identifier": "@minha_conta",
                "label": "TikTok Principal",
                "is_default": True,
                "created_at": "2026-06-24T00:17:30.144028",
            }
        }


class WorkspaceAccountsStatusResponse(BaseModel):
    workspace_id: str
    accounts: List[AccountStatusResponse] = Field(default_factory=list)

    class Config:
        json_schema_extra = {
            "example": {
                "workspace_id": "workspace-id-default",
                "accounts": [
                    {
                        "account_id": "account-id-tiktok",
                        "platform": "tiktok",
                        "name": "TikTok Principal",
                        "identifier": "@minha_conta",
                        "status": "connected",
                        "message": "Session ID do TikTok cadastrado. A validação web completa ocorre no publish.",
                        "checked_at": "2026-06-24T00:16:54.389039",
                        "expires_at": "2026-06-24T00:26:54.389039",
                        "cached": True,
                    }
                ],
            }
        }


class WorkspaceOverviewResponse(BaseModel):
    workspace: WorkspaceResponse
    accounts: List[WorkspaceAccountResponse] = Field(default_factory=list)
    account_statuses: List[AccountStatusResponse] = Field(default_factory=list)
    task_counts: Dict[str, int] = Field(default_factory=dict)

    class Config:
        json_schema_extra = {
            "example": {
                "workspace": {
                    "id": "workspace-id-default",
                    "name": "Default",
                    "slug": "default",
                    "description": "Workspace padrão criado automaticamente.",
                    "created_at": "2026-06-24T00:05:09.621848",
                    "updated_at": "2026-06-24T00:05:09.621848",
                },
                "accounts": [
                    {
                        "id": "workspace-account-link-id",
                        "workspace_id": "workspace-id-default",
                        "account_id": "account-id-tiktok",
                        "platform": "tiktok",
                        "name": "TikTok Principal",
                        "identifier": "@minha_conta",
                        "label": "TikTok Principal",
                        "is_default": True,
                        "created_at": "2026-06-24T00:17:30.144028",
                    }
                ],
                "account_statuses": [
                    {
                        "account_id": "account-id-tiktok",
                        "platform": "tiktok",
                        "name": "TikTok Principal",
                        "identifier": "@minha_conta",
                        "status": "connected",
                        "message": "Session ID do TikTok cadastrado. A validação web completa ocorre no publish.",
                        "checked_at": "2026-06-24T00:16:54.389039",
                        "expires_at": "2026-06-24T00:26:54.389039",
                        "cached": True,
                    }
                ],
                "task_counts": {
                    "queued": 0,
                    "running": 0,
                    "success": 0,
                    "error": 0,
                    "canceled": 0,
                },
            }
        }


# --- Pydantic Models para runtime ---

class ChromeBrowserStatus(BaseModel):
    browser: str
    available: bool
    source: Optional[str] = None
    executablePath: Optional[str] = None
    version: Optional[str] = None
    message: str


class TikTokBrowserStatus(BaseModel):
    usesSystemBrowser: bool
    requiredBrowser: str
    chrome: ChromeBrowserStatus
    ready: bool


class RuntimeBrowserStatusResponse(BaseModel):
    tiktok: TikTokBrowserStatus

    class Config:
        json_schema_extra = {
            "example": {
                "tiktok": {
                    "usesSystemBrowser": True,
                    "requiredBrowser": "chrome",
                    "chrome": {
                        "browser": "chrome",
                        "available": True,
                        "source": "registry",
                        "executablePath": "C:/Program Files/Google/Chrome/Application/chrome.exe",
                        "version": "150.0.7871.24",
                        "message": "Google Chrome encontrado.",
                    },
                    "ready": True,
                }
            }
        }

# --- Pydantic Models para Publicação ---

class PublishRequest(BaseModel):
    workspace_id: Optional[str] = Field(
        None,
        description="Workspace opcional que escopa a publicação e valida as contas usadas.",
    )
    mode: PublishMode = Field(
        "immediate",
        description="Use 'immediate' para publicar agora ou 'scheduled' para agendar.",
    )
    scheduled_at: Optional[datetime] = Field(
        None,
        description=(
            "Data/hora ISO 8601 para publicações agendadas. Obrigatório quando mode='scheduled'. "
            "Prefira enviar timezone explícito, por exemplo 2026-06-24T14:00:00-03:00."
        ),
    )
    video_path: str = Field(..., description="Caminho absoluto do arquivo de vídeo")
    thumb_path: Optional[str] = Field(
        None,
        description="Caminho absoluto opcional da imagem de capa/thumbnail do vídeo.",
    )
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
                "workspace_id": "workspace-id-default",
                "mode": "immediate",
                "video_path": "C:/Projetos-NestJS/OmniPublisher/1-1.mp4",
                "thumb_path": "C:/Projetos-NestJS/OmniPublisher/1-1.mp4.jpg",
                "caption": "Publicacao via OmniPublisher! #ola",
                "accounts": {
                    "tiktok": "account-id-tiktok",
                },
                "youtube_title": "Titulo do video",
                "youtube_tags": ["automacao", "video"],
                "youtube_privacy": "public",
                "instagram_format": "reels",
            }
        }

class PlatformStatus(BaseModel):
    platform: str
    status: Literal["pending", "uploading", "success", "error", "canceled"]
    progress: int = 0
    error: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "platform": "tiktok",
                "status": "uploading",
                "progress": 50,
                "error": None,
            }
        }

class TaskState(BaseModel):
    task_id: str
    platforms: Dict[str, PlatformStatus]
    created_at: datetime = Field(default_factory=datetime.utcnow)

class PublishResponse(BaseModel):
    task_id: str
    status: str
    message: str
    workspace_id: Optional[str] = None
    mode: Optional[PublishMode] = None
    scheduled_at: Optional[datetime] = None

    class Config:
        json_schema_extra = {
            "example": {
                "task_id": "publish-job-id",
                "status": "queued",
                "message": "Upload agendado. O scheduler interno disparará a publicação no horário configurado.",
                "workspace_id": "workspace-id-default",
                "mode": "scheduled",
                "scheduled_at": "2030-01-01T15:00:00",
            }
        }


class RetryPlatformResponse(PublishResponse):
    source_task_id: str
    platform: PlatformName
    retry_task: PublishResponse

    class Config:
        json_schema_extra = {
            "example": {
                "source_task_id": "publish-job-id-original",
                "platform": "tiktok",
                "task_id": "publish-job-id-retry",
                "status": "accepted",
                "message": "Retry iniciado para tiktok. Acompanhe pelo endpoint SSE.",
                "workspace_id": "workspace-id-default",
                "mode": "immediate",
                "scheduled_at": None,
                "retry_task": {
                    "task_id": "publish-job-id-retry",
                    "status": "accepted",
                    "message": "Retry iniciado para tiktok. Acompanhe pelo endpoint SSE.",
                    "workspace_id": "workspace-id-default",
                    "mode": "immediate",
                    "scheduled_at": None,
                },
            }
        }


class PublishPlatformStatusResponse(BaseModel):
    platform: str
    account_id: str
    status: str
    progress: int
    error: Optional[str] = None
    updated_at: datetime

    class Config:
        json_schema_extra = {
            "example": {
                "platform": "tiktok",
                "account_id": "account-id-tiktok",
                "status": "pending",
                "progress": 0,
                "error": None,
                "updated_at": "2026-06-24T00:17:49.908659",
            }
        }


class PublishJobEventResponse(BaseModel):
    id: str
    job_id: str
    type: str
    message: str
    payload: Optional[dict] = None
    created_at: datetime

    class Config:
        json_schema_extra = {
            "example": {
                "id": "publish-job-event-id",
                "job_id": "publish-job-id",
                "type": "job_created",
                "message": "Job criado.",
                "payload": {
                    "mode": "scheduled",
                    "status": "queued",
                },
                "created_at": "2026-06-24T00:17:49.914668",
            }
        }


class PublishJobResponse(BaseModel):
    id: str
    task_id: str
    workspace_id: Optional[str] = None
    mode: str
    status: str
    video_path: str
    thumb_path: Optional[str] = None
    caption: str
    accounts: Dict[str, str]
    youtube_title: Optional[str] = None
    youtube_tags: Optional[List[str]] = None
    youtube_privacy: str
    instagram_format: str
    scheduled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    platforms: List[PublishPlatformStatusResponse] = Field(default_factory=list)
    events: List[PublishJobEventResponse] = Field(default_factory=list)

    class Config:
        json_schema_extra = {
            "example": {
                "id": "publish-job-id",
                "task_id": "publish-job-id",
                "workspace_id": "workspace-id-default",
                "mode": "scheduled",
                "status": "queued",
                "video_path": "C:/Projetos-NestJS/OmniPublisher/1-1.mp4",
                "thumb_path": None,
                "caption": "Publicacao via OmniPublisher! #teste",
                "accounts": {
                    "tiktok": "account-id-tiktok",
                },
                "youtube_title": None,
                "youtube_tags": [],
                "youtube_privacy": "public",
                "instagram_format": "reels",
                "scheduled_at": "2030-01-01T15:00:00",
                "created_at": "2026-06-24T00:17:49.908659",
                "updated_at": "2026-06-24T00:17:49.908659",
                "started_at": None,
                "finished_at": None,
                "error": None,
                "platforms": [
                    {
                        "platform": "tiktok",
                        "account_id": "account-id-tiktok",
                        "status": "pending",
                        "progress": 0,
                        "error": None,
                        "updated_at": "2026-06-24T00:17:49.908659",
                    }
                ],
                "events": [
                    {
                        "id": "publish-job-event-id",
                        "job_id": "publish-job-id",
                        "type": "job_created",
                        "message": "Job criado.",
                        "payload": {
                            "mode": "scheduled",
                            "status": "queued",
                        },
                        "created_at": "2026-06-24T00:17:49.914668",
                    }
                ],
            }
        }
