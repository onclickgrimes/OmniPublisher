import os
import json
from http.cookies import SimpleCookie
# pyrefly: ignore [missing-import]
from google.oauth2.credentials import Credentials
# pyrefly: ignore [missing-import]
from google_auth_oauthlib.flow import InstalledAppFlow
# pyrefly: ignore [missing-import]
from google.auth.transport.requests import Request
# pyrefly: ignore [missing-import]
from instagrapi import Client
# pyrefly: ignore [missing-import]
from instagrapi.exceptions import ChallengeRequired, TwoFactorRequired
from typing import Any, Dict

from app.config import SESSIONS_DIR, YOUTUBE_CLIENT_SECRETS_FILE, YOUTUBE_OAUTH_PORT
from app.models.db import SessionLocal, Account


class InstagramAuthChallengeRequired(Exception):
    def __init__(self, message: str, *, raw: dict[str, Any] | None = None):
        super().__init__(message)
        self.raw = raw or {}


def _extract_cookie_value(value: str, cookie_name: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if f"{cookie_name}=" not in value:
        return value

    cookies = SimpleCookie()
    cookies.load(value)
    morsel = cookies.get(cookie_name)
    return morsel.value.strip() if morsel else ""


def _looks_like_instagram_sessionid(value: str) -> bool:
    normalized = _extract_cookie_value(value, "sessionid")
    return "sessionid=" in str(value or "") or "%3A" in normalized or normalized.count(":") >= 2


class SessionManager:
    """
    Gerencia a persistência de autenticação associada às contas.
    """

    def __init__(self):
        self.youtube_scopes = ["https://www.googleapis.com/auth/youtube.upload"]
        # Cache em memória para instâncias do instagrapi por conta
        self.instagram_clients: Dict[str, Client] = {}
        self.instagram_pending_clients: Dict[str, Client] = {}

    def get_account(self, account_id: str) -> Account:
        db = SessionLocal()
        try:
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                raise ValueError(f"Conta {account_id} não encontrada no banco de dados.")
            return account
        finally:
            db.close()

    # --- Meta Graph API (Instagram Business/Creator) ---

    def has_graph_api(self, account_id: str) -> bool:
        """Verifica se a conta tem token Graph API válido (dual-auth)."""
        account = self.get_account(account_id)
        from datetime import datetime
        return bool(
            account.graph_token
            and account.ig_business_id
            and (not account.graph_token_expires_at or account.graph_token_expires_at > datetime.utcnow())
        )

    def get_graph_api_config(self, account_id: str) -> dict:
        """Retorna config da Graph API para a conta."""
        account = self.get_account(account_id)
        if not account.graph_token:
            raise ValueError("Conta não possui token da Graph API.")
        return {
            "access_token": account.graph_token,
            "ig_business_id": account.ig_business_id,
            "fb_page_id": account.fb_page_id,
            "fb_page_token": account.fb_page_token,
        }

    def get_facebook_page_config(self, account_id: str) -> dict:
        """Retorna config para publicação direta em uma Página Facebook."""
        account = self.get_account(account_id)
        if account.platform != "facebook":
            raise ValueError("ID informado não pertence a uma conta do Facebook.")
        if not account.fb_page_id or not account.fb_page_token:
            raise ValueError(
                "A conta não possui uma Página do Facebook vinculada ou token de acesso de página (Page Access Token) válido."
            )
        return {
            "fb_page_id": account.fb_page_id,
            "fb_page_token": account.fb_page_token,
            "fb_page_name": account.fb_page_name,
        }

    def save_graph_api_tokens(self, account_id: str, oauth_data: dict):
        """Salva tokens OAuth da Graph API na conta existente (mantendo credenciais instagrapi)."""
        db = SessionLocal()
        try:
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                raise ValueError(f"Conta {account_id} não encontrada.")

            account.graph_token = oauth_data.get("access_token")
            account.graph_token_expires_at = oauth_data.get("expires_at")
            account.ig_business_id = oauth_data.get("ig_business_id")
            account.fb_page_id = oauth_data.get("fb_page_id")
            account.fb_page_token = oauth_data.get("fb_page_token")
            account.fb_page_name = oauth_data.get("fb_page_name")
            account.account_type = oauth_data.get("account_type", "business")
            db.commit()
        finally:
            db.close()

    # --- YouTube ---

    def get_youtube_credentials(self, account_id: str) -> Credentials:
        account = self.get_account(account_id)
        if account.platform != "youtube":
            raise ValueError("ID informado não pertence a uma conta do YouTube.")

        token_file = SESSIONS_DIR / account.settings_file
        creds = None

        if os.path.exists(token_file):
            try:
                creds = Credentials.from_authorized_user_file(token_file, self.youtube_scopes)
            except Exception as e:
                print(f"Erro ao ler token para conta {account.name}: {e}")

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    print(f"Erro ao atualizar token, solicitando novo login: {e}")
                    creds = self._youtube_manual_auth()
            else:
                creds = self._youtube_manual_auth()

            with open(token_file, 'w') as token:
                token.write(creds.to_json())

        return creds

    def _youtube_manual_auth(self) -> Credentials:
        if not os.path.exists(YOUTUBE_CLIENT_SECRETS_FILE):
            raise FileNotFoundError(
                f"O arquivo {YOUTUBE_CLIENT_SECRETS_FILE} não foi encontrado."
            )
        flow = InstalledAppFlow.from_client_secrets_file(YOUTUBE_CLIENT_SECRETS_FILE, self.youtube_scopes)
        return flow.run_local_server(port=YOUTUBE_OAUTH_PORT)

    def clear_instagram_client(self, account_id: str):
        self.instagram_clients.pop(account_id, None)
        self.instagram_pending_clients.pop(account_id, None)

    def get_instagram_client(self, account_id: str, *, challenge_code: str | None = None) -> Client:
        challenge_code = str(challenge_code or "").strip()
        if account_id in self.instagram_clients and not challenge_code:
            return self.instagram_clients[account_id]

        account = self.get_account(account_id)
        if account.platform != "instagram":
            raise ValueError("ID informado não pertence a uma conta do Instagram.")

        if not account.identifier or not account.credentials:
            raise ValueError("Conta do Instagram sem username ou senha cadastrados no DB.")

        settings_name = account.settings_file or f"instagram_settings_{account.id}.json"
        settings_file = SESSIONS_DIR / settings_name
        pending_client = self.instagram_pending_clients.get(account_id)

        def _challenge_code_handler(username: str, choice=None):
            return challenge_code or None

        def _raise_challenge(client: Client, exc: Exception):
            try:
                client.dump_settings(settings_file)
            except Exception:
                pass
            self.instagram_pending_clients[account_id] = client
            raw = getattr(client, "last_json", None)
            raise InstagramAuthChallengeRequired(str(exc), raw=raw if isinstance(raw, dict) else {}) from exc

        def _login(client: Client) -> Client:
            client.challenge_code_handler = _challenge_code_handler
            try:
                client.login(
                    account.identifier,
                    account.credentials,
                    verification_code=challenge_code,
                )
            except (ChallengeRequired, TwoFactorRequired) as exc:
                _raise_challenge(client, exc)
            client.dump_settings(settings_file)
            self.instagram_pending_clients.pop(account_id, None)
            self.instagram_clients[account_id] = client
            return client

        if pending_client is not None:
            return _login(pending_client)

        cl = Client()
        if os.path.exists(settings_file) and not challenge_code:
            cl.load_settings(settings_file)
            try:
                try:
                    cl.account_info()
                except TypeError:
                    pass
                self.instagram_pending_clients.pop(account_id, None)
                self.instagram_clients[account_id] = cl
                return cl
            except Exception as e:
                print(f"Sessão salva do Instagram expirou para {account.name}: {e}. Refazendo login...")
                if _looks_like_instagram_sessionid(account.credentials):
                    try:
                        cl.login_by_sessionid(_extract_cookie_value(account.credentials, "sessionid"))
                        cl.dump_settings(settings_file)
                        self.instagram_pending_clients.pop(account_id, None)
                        self.instagram_clients[account_id] = cl
                        return cl
                    except Exception as session_exc:
                        print(f"Falha ao reutilizar sessionid do Instagram para {account.name}: {session_exc}")
        elif os.path.exists(settings_file):
            cl.load_settings(settings_file)
            try:
                return _login(cl)
            except InstagramAuthChallengeRequired:
                raise
            except Exception as e:
                print(f"Sessão do Instagram expirou para {account.name}: {e}. Refazendo login...")

        if not challenge_code and _looks_like_instagram_sessionid(account.credentials):
            cl = Client()
            cl.login_by_sessionid(_extract_cookie_value(account.credentials, "sessionid"))
            cl.dump_settings(settings_file)
            self.instagram_pending_clients.pop(account_id, None)
            self.instagram_clients[account_id] = cl
            return cl

        return _login(Client())

    def set_instagram_sessionid(self, account_id: str, sessionid: str) -> Client:
        account = self.get_account(account_id)
        if account.platform != "instagram":
            raise ValueError("ID informado não pertence a uma conta do Instagram.")

        normalized_sessionid = _extract_cookie_value(sessionid, "sessionid")
        if not normalized_sessionid:
            raise ValueError("Cookie sessionid do Instagram é obrigatório.")

        settings_name = account.settings_file or f"instagram_settings_{account.id}.json"
        settings_file = SESSIONS_DIR / settings_name

        cl = Client()
        if os.path.exists(settings_file):
            try:
                cl.load_settings(settings_file)
            except Exception:
                cl = Client()

        cl.login_by_sessionid(normalized_sessionid)
        cl.dump_settings(settings_file)
        self.instagram_pending_clients.pop(account_id, None)
        self.instagram_clients[account_id] = cl
        return cl

# Instância global
session_manager = SessionManager()
