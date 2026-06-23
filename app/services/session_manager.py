import os
import json
# pyrefly: ignore [missing-import]
from google.oauth2.credentials import Credentials
# pyrefly: ignore [missing-import]
from google_auth_oauthlib.flow import InstalledAppFlow
# pyrefly: ignore [missing-import]
from google.auth.transport.requests import Request
# pyrefly: ignore [missing-import]
from instagrapi import Client
from typing import Dict

from app.config import SESSIONS_DIR, YOUTUBE_CLIENT_SECRETS_FILE, YOUTUBE_OAUTH_PORT
from app.models.db import SessionLocal, Account

class SessionManager:
    """
    Gerencia a persistência de autenticação associada às contas.
    """

    def __init__(self):
        self.youtube_scopes = ["https://www.googleapis.com/auth/youtube.upload"]
        # Cache em memória para instâncias do instagrapi por conta
        self.instagram_clients: Dict[str, Client] = {}

    def get_account(self, account_id: str) -> Account:
        db = SessionLocal()
        try:
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                raise ValueError(f"Conta {account_id} não encontrada no banco de dados.")
            return account
        finally:
            db.close()

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

    def get_instagram_client(self, account_id: str) -> Client:
        if account_id in self.instagram_clients:
            return self.instagram_clients[account_id]

        account = self.get_account(account_id)
        if account.platform != "instagram":
            raise ValueError("ID informado não pertence a uma conta do Instagram.")

        if not account.identifier or not account.credentials:
            raise ValueError("Conta do Instagram sem username ou senha cadastrados no DB.")

        settings_file = SESSIONS_DIR / account.settings_file
        cl = Client()
        
        if os.path.exists(settings_file):
            cl.load_settings(settings_file)
            try:
                cl.login(account.identifier, account.credentials)
                self.instagram_clients[account_id] = cl
                return cl
            except Exception as e:
                print(f"Sessão do Instagram expirou para {account.name}: {e}. Refazendo login...")

        cl.login(account.identifier, account.credentials)
        cl.dump_settings(settings_file)
        self.instagram_clients[account_id] = cl
        return cl

# Instância global
session_manager = SessionManager()
