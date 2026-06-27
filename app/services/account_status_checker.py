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
from app.services.session_manager import InstagramAuthChallengeRequired, session_manager


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


def _unknown_status_payload(account: Account, message: str) -> dict[str, Any]:
    now = _utc_now()
    return {
        "account_id": account.id,
        "platform": account.platform,
        "name": account.name,
        "identifier": account.identifier,
        "status": "unknown",
        "message": message,
        "checked_at": now,
        "expires_at": now,
        "cached": False,
    }


def _instagram_challenge_message(account_id: str, exc: InstagramAuthChallengeRequired) -> str:
    raw = exc.raw if isinstance(exc.raw, dict) else {}
    step_name = str(raw.get("step_name") or "")
    step_data = raw.get("step_data") if isinstance(raw.get("step_data"), dict) else {}
    code_steps = {
        "verify_email",
        "verify_email_code",
        "verify_phone",
        "verify_phone_code",
        "verify_sms",
        "verify_sms_code",
        "select_verify_method",
        "select_contact_point_recovery",
    }
    has_contact_choice = any(key in step_data for key in ["email", "phone_number"])

    if step_name in code_steps or has_contact_choice:
        return (
            "Instagram pediu verificação por código. Envie o código recebido "
            f"para POST /accounts/{account_id}/challenge. Detalhe: {exc}"
        )

    return (
        "Instagram pediu um checkpoint manual e não disponibilizou etapa de código "
        "para esta tentativa. Abra o Instagram no app ou navegador em um dispositivo "
        "confiável, conclua a verificação da conta e depois rode status?refresh=true "
        f"novamente. Detalhe: {exc}"
    )


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
            check = self._persist_check(db, account, result, checked_at=now, expires_at=expires_at)
            return _status_payload(account, check, cached=False)
        finally:
            db.close()

    def submit_instagram_challenge(self, account_id: str, code: str) -> dict[str, Any] | None:
        code = str(code or "").strip()
        if not code:
            raise ValueError("Código do Instagram é obrigatório.")

        db = SessionLocal()
        try:
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                return None
            if account.platform != "instagram":
                raise ValueError("A conta informada não é do Instagram.")

            now = _utc_now()
            try:
                client = session_manager.get_instagram_client(account.id, challenge_code=code)
                try:
                    client.account_info()
                except TypeError:
                    pass
                result = {
                    "status": "connected",
                    "message": "Sessão do Instagram válida após verificação.",
                }
            except InstagramAuthChallengeRequired as exc:
                result = {
                    "status": "challenge_required",
                    "message": _instagram_challenge_message(account.id, exc),
                    "raw": exc.raw,
                }
            except Exception as exc:
                result = {
                    "status": "needs_auth",
                    "message": f"Falha ao concluir verificação do Instagram: {exc}",
                }

            check = self._persist_check(
                db,
                account,
                result,
                checked_at=now,
                expires_at=now + timedelta(seconds=max(1, ACCOUNT_STATUS_CACHE_TTL_SECONDS)),
            )
            return _status_payload(account, check, cached=False)
        finally:
            db.close()

    def submit_instagram_sessionid(self, account_id: str, sessionid: str) -> dict[str, Any] | None:
        sessionid = str(sessionid or "").strip()
        if not sessionid:
            raise ValueError("Cookie sessionid do Instagram é obrigatório.")

        db = SessionLocal()
        try:
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                return None
            if account.platform != "instagram":
                raise ValueError("A conta informada não é do Instagram.")

            now = _utc_now()
            try:
                client = session_manager.set_instagram_sessionid(account.id, sessionid)
                try:
                    client.account_info()
                except TypeError:
                    pass
                result = {
                    "status": "connected",
                    "message": "Sessão do Instagram importada via cookie sessionid.",
                }
            except Exception as exc:
                result = {
                    "status": "needs_auth",
                    "message": f"Falha ao importar sessionid do Instagram: {exc}",
                }

            check = self._persist_check(
                db,
                account,
                result,
                checked_at=now,
                expires_at=now + timedelta(seconds=max(1, ACCOUNT_STATUS_CACHE_TTL_SECONDS)),
            )
            return _status_payload(account, check, cached=False)
        finally:
            db.close()

    def get_cached_account_status(self, account_id: str) -> dict[str, Any] | None:
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
            if latest and latest.expires_at > now:
                return _status_payload(account, latest, cached=True)

            if latest:
                return _unknown_status_payload(account, "Status cacheado expirou.")
            return _unknown_status_payload(account, "Conta ainda não verificada.")
        finally:
            db.close()

    def mark_account_checking(self, account_id: str) -> dict[str, Any] | None:
        db = SessionLocal()
        try:
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                return None

            now = _utc_now()
            check = AccountStatusCheck(
                account_id=account.id,
                status="checking",
                message="Verificação de status em andamento.",
                checked_at=now,
                expires_at=now + timedelta(seconds=max(1, ACCOUNT_STATUS_CACHE_TTL_SECONDS)),
                raw_json=_dumps_json({}),
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

    def get_workspace_accounts_cached_status(self, workspace_id: str) -> dict[str, Any] | None:
        account_ids = self._workspace_account_ids(workspace_id)
        if account_ids is None:
            return None

        statuses = []
        for account_id in account_ids:
            status = self.get_cached_account_status(account_id)
            if status:
                statuses.append(status)

        return {
            "workspace_id": workspace_id,
            "accounts": statuses,
        }

    def mark_workspace_accounts_checking(self, workspace_id: str) -> dict[str, Any] | None:
        account_ids = self._workspace_account_ids(workspace_id)
        if account_ids is None:
            return None

        statuses = []
        for account_id in account_ids:
            status = self.mark_account_checking(account_id)
            if status:
                statuses.append(status)

        return {
            "workspace_id": workspace_id,
            "accounts": statuses,
        }

    def refresh_workspace_accounts(self, workspace_id: str):
        account_ids = self._workspace_account_ids(workspace_id)
        if account_ids is None:
            return
        for account_id in account_ids:
            self.check_account_status(account_id, refresh=True)

    def _workspace_account_ids(self, workspace_id: str) -> list[str] | None:
        db = SessionLocal()
        try:
            workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
            if not workspace:
                return None
            return [
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
        except InstagramAuthChallengeRequired as exc:
            return {
                "status": "challenge_required",
                "message": _instagram_challenge_message(account.id, exc),
                "raw": exc.raw,
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

    def _persist_check(
        self,
        db,
        account: Account,
        result: dict[str, Any],
        *,
        checked_at: datetime,
        expires_at: datetime,
    ) -> AccountStatusCheck:
        check = AccountStatusCheck(
            account_id=account.id,
            status=result["status"],
            message=result.get("message"),
            checked_at=checked_at,
            expires_at=expires_at,
            raw_json=_dumps_json(result.get("raw") or {}),
        )
        db.add(check)
        db.commit()
        db.refresh(check)
        return check


account_status_checker = AccountStatusChecker()
