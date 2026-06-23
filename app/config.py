import os
from pathlib import Path
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

APP_NAME = "OmniPublisher"
APP_VERSION = "1.0.0"

# Caminhos base
BASE_DIR = Path(__file__).resolve().parent.parent

# Carrega variáveis de ambiente do arquivo .env na raiz do projeto
load_dotenv(BASE_DIR / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _path_from_env(name: str, default: Path) -> Path:
    value = os.getenv(name)
    path = Path(value) if value else default
    return path.expanduser().resolve()


OMNIPUBLISHER_HOST = os.getenv("OMNIPUBLISHER_HOST", "127.0.0.1")
OMNIPUBLISHER_PORT = _env_int("OMNIPUBLISHER_PORT", 7813)
OMNIPUBLISHER_PUBLIC_BASE_URL = os.getenv(
    "OMNIPUBLISHER_PUBLIC_BASE_URL",
    f"http://{OMNIPUBLISHER_HOST}:{OMNIPUBLISHER_PORT}",
)

DATA_DIR = _path_from_env("OMNIPUBLISHER_DATA_DIR", BASE_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)

SESSIONS_DIR = _path_from_env("OMNIPUBLISHER_SESSIONS_DIR", DATA_DIR / "sessions")

# Garante que o diretório de sessões existe
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = _path_from_env("OMNIPUBLISHER_DB_PATH", DATA_DIR / "omnipublisher.db")
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"


def _resolve_client_secret_path() -> Path:
    raw_value = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json")
    configured_path = Path(raw_value).expanduser()
    if configured_path.is_absolute():
        return configured_path.resolve()

    project_path = (BASE_DIR / configured_path).resolve()
    if project_path.exists():
        return project_path

    return (DATA_DIR / configured_path).resolve()


# Configurações do YouTube
YOUTUBE_CLIENT_SECRETS_FILE = str(_resolve_client_secret_path())
YOUTUBE_TOKEN_FILE = SESSIONS_DIR / "youtube_token.json"
YOUTUBE_OAUTH_PORT = _env_int("YOUTUBE_OAUTH_PORT", 8080)

# Configurações do Instagram
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD")
INSTAGRAM_SETTINGS_FILE = SESSIONS_DIR / "instagram_settings.json"

# Outras configurações
TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "300"))  # 5 minutos por padrão
