import json
import os
from datetime import datetime, timedelta
from typing import Any

# pyrefly: ignore [missing-import]
from google.auth.transport.requests import Request
# pyrefly: ignore [missing-import]
from google.oauth2.credentials import Credentials

from app.config import ACCOUNT_STATUS_CACHE_TTL_SECONDS, SESSIONS_DIR
from app.models.db import Account, AccountStatusCheck, SessionLocal, Workspace, WorkspaceAccount
from app.services.session_manager import session_manager


def _utc_now() -> datetime:
    return datetime.utcnow()


def _dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads_json(value: str | None, fallback: Any):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _status_payload(account: Account, check: AccountStatusCheck, *, cached: bool) -> dict[str, Any]:
    raw = _loads_json(check.raw_json, {})
    return {
        "account_id": account.id,
        "platform": account.platform,
        "name": account.name,
        "identifier": account.identifier,
        "status": check.status,
        "message": check.message,
        "checked_at": check.checked_at,
        "expires_at": check.expires_at,
        "cached": cached,
        "raw": raw,
    }


class AccountStatusChecker:
    """
    Verifica e cacheia o estado de autenticação das contas.
    """

    def check_account_status(self, account_id: str, *, refresh: bool = False) -> dict[str, Any] | None:
        db = SessionLocal()
        try:
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                return None

            now = _utc_now()
            latest = (
                db.query(AccountStatusCheck)
                .filter(AccountStatusCheck.account_id == account_id)
                .order_by(AccountStatusCheck.checked_at.desc())
                .first()
            )
            if latest and not refresh and latest.expires_at > now:
                return _status_payload(account, latest, cached=True)

            result = self._probe_account(account)
            expires_at = now + timedelta(seconds=max(1, ACCOUNT_STATUS_CACHE_TTL_SECONDS))
            check = AccountStatusCheck(
                account_id=account.id,
                status=result["status"],
                message=result.get("message"),
                checked_at=now,
                expires_at=expires_at,
                raw_json=_dumps_json(result.get("raw") or {}),
            )
            db.add(check)
            db.commit()
            db.refresh(check)
            return _status_payload(account, check, cached=False)
        finally:
            db.close()

    def check_workspace_accounts_status(
        self,
        workspace_id: str,
        *,
        refresh: bool = False,
    ) -> dict[str, Any] | None:
        db = SessionLocal()
        try:
            workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
            if not workspace:
                return None

            account_ids = [
                row.account_id
                for row in (
                    db.query(WorkspaceAccount)
                    .filter(WorkspaceAccount.workspace_id == workspace_id)
                    .order_by(WorkspaceAccount.created_at.asc())
                    .all()
                )
            ]
        finally:
            db.close()

        statuses = []
        for account_id in account_ids:
            status = self.check_account_status(account_id, refresh=refresh)
            if status:
                statuses.append(status)

        return {
            "workspace_id": workspace_id,
            "accounts": statuses,
        }

    def _probe_account(self, account: Account) -> dict[str, Any]:
        if account.platform == "youtube":
            return self._probe_youtube(account)
        if account.platform == "instagram":
            return self._probe_instagram(account)
        if account.platform == "tiktok":
            return self._probe_tiktok(account)
        return {
            "status": "error",
            "message": f"Plataforma '{account.platform}' não suportada.",
        }

    def _probe_youtube(self, account: Account) -> dict[str, Any]:
        if not account.settings_file:
            return {
                "status": "needs_auth",
                "message": "Conta do YouTube sem arquivo de token configurado.",
            }

        token_file = SESSIONS_DIR / account.settings_file
        if not os.path.exists(token_file):
            return {
                "status": "needs_auth",
                "message": "OAuth do YouTube ainda não autorizado para esta conta.",
            }

        try:
            creds = Credentials.from_authorized_user_file(token_file, session_manager.youtube_scopes)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_file, "w", encoding="utf-8") as token:
                    token.write(creds.to_json())

            if creds and creds.valid:
                return {
                    "status": "connected",
                    "message": "Token OAuth do YouTube válido.",
                }

            return {
                "status": "needs_auth",
                "message": "Token OAuth do YouTube inválido ou expirado sem refresh token.",
            }
        except Exception as exc:
            return {
                "status": "needs_auth",
                "message": f"Falha ao validar OAuth do YouTube: {exc}",
            }

    def _probe_instagram(self, account: Account) -> dict[str, Any]:
        if not account.identifier or not account.credentials:
            return {
                "status": "needs_auth",
                "message": "Conta do Instagram sem usuário ou senha cadastrados.",
            }

        try:
            client = session_manager.get_instagram_client(account.id)
            try:
                client.account_info()
            except TypeError:
                # Algumas versões do instagrapi expõem account_info com assinatura distinta;
                # o login acima já valida a sessão para o caso de uso atual.
                pass
            return {
                "status": "connected",
                "message": "Sessão do Instagram válida.",
            }
        except Exception as exc:
            return {
                "status": "needs_auth",
                "message": f"Sessão do Instagram inválida ou login falhou: {exc}",
            }

    def _probe_tiktok(self, account: Account) -> dict[str, Any]:
        if not account.credentials:
            return {
                "status": "needs_auth",
                "message": "Conta do TikTok sem session_id cadastrado.",
            }

        try:
            # pyrefly: ignore [missing-import]
            import tiktok_uploader.upload  # noqa: F401
        except ImportError:
            return {
                "status": "error",
                "message": "Módulo tiktok_uploader não encontrado.",
            }

        return {
            "status": "connected",
            "message": "Session ID do TikTok cadastrado. A validação web completa ocorre no publish.",
        }


account_status_checker = AccountStatusChecker()
