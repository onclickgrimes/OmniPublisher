# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware

from app.routes import publish, tasks, accounts
from app.services.session_manager import session_manager
from app.services.scheduler import scheduler
from app.services.task_manager import task_manager
from app.models.db import engine, Base, ensure_database_schema
from app.config import (
    APP_NAME,
    APP_VERSION,
    DATA_DIR,
    DATABASE_PATH,
    OMNIPUBLISHER_HOST,
    OMNIPUBLISHER_PORT,
    OMNIPUBLISHER_PUBLIC_BASE_URL,
    RUNNING_JOB_STALE_MINUTES,
    SCHEDULER_INTERVAL_SECONDS,
    SESSIONS_DIR,
    YOUTUBE_CLIENT_SECRETS_FILE,
    YOUTUBE_OAUTH_PORT,
)

# Cria as tabelas do SQLite no banco (se não existirem)
Base.metadata.create_all(bind=engine)
ensure_database_schema()

app = FastAPI(
    title=APP_NAME,
    description="Motor centralizado de postagem omnichannel (YouTube, Instagram, TikTok)",
    version=APP_VERSION,
)

# Configura CORS - Permissivo para facilitar consumo local (frontend React/Vue/Angular etc)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite de qualquer origem
    allow_credentials=True,
    allow_methods=["*"],  # Permite GET, POST, OPTIONS, etc.
    allow_headers=["*"],
)

# Adiciona as rotas
app.include_router(publish.router, tags=["Publish"])
app.include_router(tasks.router, tags=["Tasks"])
app.include_router(accounts.router, prefix="/accounts", tags=["Accounts"])

@app.on_event("startup")
async def startup_event():
    """
    Executado ao iniciar a aplicação.
    """
    recovered = task_manager.recover_interrupted_jobs(RUNNING_JOB_STALE_MINUTES)
    if recovered:
        print(f"{len(recovered)} job(s) running antigo(s) marcados como error no startup.")
    await scheduler.start()
    print(f"{APP_NAME} iniciado com sucesso!")


@app.on_event("shutdown")
async def shutdown_event():
    """
    Executado ao encerrar a aplicação.
    """
    await scheduler.stop()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "omnipublisher",
        "version": APP_VERSION,
    }


@app.get("/version")
def version():
    return {
        "service": "omnipublisher",
        "version": APP_VERSION,
    }


@app.get("/runtime")
def runtime():
    return {
        "service": "omnipublisher",
        "version": APP_VERSION,
        "host": OMNIPUBLISHER_HOST,
        "port": OMNIPUBLISHER_PORT,
        "publicBaseUrl": OMNIPUBLISHER_PUBLIC_BASE_URL,
        "dataDir": str(DATA_DIR),
        "databasePath": str(DATABASE_PATH),
        "sessionsDir": str(SESSIONS_DIR),
        "youtubeClientSecretsFile": str(YOUTUBE_CLIENT_SECRETS_FILE),
        "youtubeOauthPort": YOUTUBE_OAUTH_PORT,
        "schedulerIntervalSeconds": SCHEDULER_INTERVAL_SECONDS,
        "runningJobStaleMinutes": RUNNING_JOB_STALE_MINUTES,
    }

@app.get("/")
def read_root():
    return {
        "message": f"Bem vindo ao {APP_NAME} API",
        "service": "omnipublisher",
        "version": APP_VERSION,
        "docs": "Acesse /docs para a documentação interativa."
    }
