from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
import httpx
from datetime import datetime, timedelta
import secrets
import threading
import urllib.parse

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

from app.config import CLOUDFLARE_TUNNEL_LOGIN_TTL_SECONDS
from app.models.db import Account, IntegrationConfig, get_db
from app.services.cloudflare_tunnel import cloudflare_tunnel_manager
from app.services.session_manager import session_manager

router = APIRouter()

_pending_oauth_states: dict[str, dict[str, str]] = {}
_pending_oauth_lock = threading.RLock()

# Escopos do Instagram API with Instagram Login.
# O fluxo antigo via Facebook Login usava instagram_basic/instagram_content_publish,
# mas apps novos da Instagram Platform usam os escopos instagram_business_*.
META_SCOPES = [
    "instagram_business_basic",
    "instagram_business_content_publish",
]


def _create_oauth_state(
    account_id: str,
    lease_id: str,
    redirect_uri: str,
    integration_id: str,
    instagram_app_id: str,
) -> str:
    state = secrets.token_urlsafe(32)
    with _pending_oauth_lock:
        _pending_oauth_states[state] = {
            "account_id": account_id,
            "lease_id": lease_id,
            "redirect_uri": redirect_uri,
            "integration_id": integration_id,
            "instagram_app_id": instagram_app_id,
        }
    return state


def _consume_oauth_state(state: str) -> dict[str, str] | None:
    with _pending_oauth_lock:
        return _pending_oauth_states.pop(state, None)


def _get_meta_integration(db: Session) -> IntegrationConfig | None:
    return (
        db.query(IntegrationConfig)
        .filter(IntegrationConfig.provider == "meta")
        .first()
    )


def _require_meta_integration(db: Session) -> IntegrationConfig:
    config = _get_meta_integration(db)
    if (
        not config
        or not str(config.instagram_app_id or "").strip()
        or not str(config.instagram_app_secret or "").strip()
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Integração Meta incompleta. Cadastre instagram_app_id e "
                "instagram_app_secret em /integrations/meta antes de iniciar o login."
            ),
        )
    return config


@router.get("/login")
def meta_login(account_id: str, db: Session = Depends(get_db)):
    """
    Gera a URL de autorização OAuth do Instagram para uma conta específica
    e redireciona o usuário para lá.
    """
    return RedirectResponse(_prepare_meta_login(account_id, db)["auth_url"])


@router.get("/login-info")
def meta_login_info(account_id: str, db: Session = Depends(get_db)):
    """
    Prepara o OAuth e retorna os dados necessários para cadastrar manualmente
    o redirect URI randômico antes de abrir o login no navegador.
    """
    return _prepare_meta_login(account_id, db)


def _prepare_meta_login(account_id: str, db: Session) -> dict:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    if account.platform != "instagram":
        raise HTTPException(status_code=400, detail="A conexão Instagram OAuth só suporta contas Instagram no momento.")

    meta_config = _require_meta_integration(db)

    try:
        lease = cloudflare_tunnel_manager.acquire(
            "instagram_oauth",
            ttl_seconds=CLOUDFLARE_TUNNEL_LOGIN_TTL_SECONDS,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    redirect_uri = f"{lease.public_url.rstrip('/')}/api/auth/facebook/callback"
    state = _create_oauth_state(
        account_id,
        lease.lease_id,
        redirect_uri,
        meta_config.id,
        meta_config.instagram_app_id,
    )

    params = {
        "client_id": meta_config.instagram_app_id,
        "redirect_uri": redirect_uri,
        "scope": ",".join(META_SCOPES),
        "response_type": "code",
        "enable_fb_login": "0",
        "force_authentication": "1",
        "version": "v22.0",
        "state": state,
    }

    auth_url = f"https://www.instagram.com/oauth/authorize?{urllib.parse.urlencode(params)}"
    return {
        "account_id": account_id,
        "provider": "instagram_graph",
        "auth_url": auth_url,
        "redirect_uri": redirect_uri,
        "domain": urllib.parse.urlparse(redirect_uri).netloc,
        "scopes": META_SCOPES,
        "expires_in_seconds": CLOUDFLARE_TUNNEL_LOGIN_TTL_SECONDS,
    }


@router.get("/callback")
async def meta_callback(code: str = Query(None), state: str = Query(None), error: str = Query(None), error_description: str = Query(None), db: Session = Depends(get_db)):
    """
    Rota de retorno do OAuth do Instagram.
    Recebe o código de autorização, troca por tokens de longa duração e salva
    os dados na conta referenciada pelo parametro state.
    """
    pending_state = _consume_oauth_state(state) if state else None

    if error:
        if pending_state:
            cloudflare_tunnel_manager.release(pending_state.get("lease_id"))
        raise HTTPException(status_code=400, detail=f"Erro na autorização: {error_description or error}")

    if not code or not state or not pending_state:
        raise HTTPException(status_code=400, detail="Código de autorização ou state ausente.")

    account_id = pending_state["account_id"]
    redirect_uri = pending_state["redirect_uri"]
    lease_id = pending_state["lease_id"]
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        cloudflare_tunnel_manager.release(lease_id)
        raise HTTPException(status_code=404, detail="A conta original (state) não foi encontrada.")

    meta_config = (
        db.query(IntegrationConfig)
        .filter(IntegrationConfig.id == pending_state.get("integration_id"))
        .first()
    )
    if (
        not meta_config
        or not meta_config.instagram_app_id
        or not meta_config.instagram_app_secret
    ):
        cloudflare_tunnel_manager.release(lease_id)
        raise HTTPException(
            status_code=409,
            detail=(
                "Integração Meta usada no início do login não está mais configurada. "
                "Cadastre /integrations/meta e reinicie o login."
            ),
        )
    if meta_config.instagram_app_id != pending_state.get("instagram_app_id"):
        cloudflare_tunnel_manager.release(lease_id)
        raise HTTPException(
            status_code=409,
            detail=(
                "O ID do app do Instagram mudou durante o login. "
                "Reinicie o OAuth para usar a configuração atual."
            ),
        )

    release_delay = 0
    try:
        async with httpx.AsyncClient() as client:
            # 1. Trocar o 'code' por um short-lived Instagram access token.
            token_res = await client.post(
                "https://api.instagram.com/oauth/access_token",
                data={
                    "client_id": meta_config.instagram_app_id,
                    "redirect_uri": redirect_uri,
                    "client_secret": meta_config.instagram_app_secret,
                    "grant_type": "authorization_code",
                    "code": code
                },
                timeout=10.0
            )
            if token_res.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Falha ao obter access token: {token_res.text}")

            token_data = token_res.json()
            short_lived_token = token_data.get("access_token")
            instagram_user_id = token_data.get("user_id")
            if not short_lived_token:
                raise HTTPException(status_code=400, detail=f"Resposta OAuth sem access_token: {token_data}")

            # 2. Trocar short-lived por long-lived Instagram access token.
            long_token_res = await client.get(
                "https://graph.instagram.com/access_token",
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": meta_config.instagram_app_secret,
                    "access_token": short_lived_token
                },
                timeout=10.0
            )
            if long_token_res.status_code != 200:
                # Fallback para o short-lived se a troca falhar
                long_lived_token = short_lived_token
                expires_in = token_data.get("expires_in", 3600)
            else:
                long_token_data = long_token_res.json()
                long_lived_token = long_token_data.get("access_token")
                expires_in = long_token_data.get("expires_in", 5184000) # Padrão: 60 dias

            expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

            # 3. Descobrir o Instagram User ID e tipo da conta autorizada.
            profile_res = await client.get(
                "https://graph.instagram.com/me",
                params={
                    "fields": "id,username,account_type",
                    "access_token": long_lived_token,
                },
                timeout=10.0
            )
            if profile_res.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Falha ao obter perfil Instagram: {profile_res.text}")

            profile_data = profile_res.json()
            ig_business_id = profile_data.get("id") or instagram_user_id
            if not ig_business_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Não foi possível obter o ID da conta Instagram autorizada: {profile_data}"
                )

            account_type = str(profile_data.get("account_type") or "business").lower()

            # 4. Salvar tudo no BD usando o session_manager.
            # Este login direto do Instagram não retorna Page Access Token; publicação
            # na Página Facebook continua exigindo um fluxo Facebook/Page separado.
            oauth_data = {
                "access_token": long_lived_token,
                "expires_at": expires_at,
                "ig_business_id": ig_business_id,
                "fb_page_id": None,
                "fb_page_token": None,
                "fb_page_name": None,
                "account_type": account_type,
            }
            session_manager.save_graph_api_tokens(account_id, oauth_data)

        release_delay = 10
        # Redirecionar para o frontend com sucesso
        # Em produção, essa URL viria das configurações ou do frontend, mas como é um app desktop local:
        return RedirectResponse(f"/#/?oauth_success=true&account_id={account_id}")
    finally:
        cloudflare_tunnel_manager.release(lease_id, delay_seconds=release_delay)
