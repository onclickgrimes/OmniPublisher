from __future__ import annotations

from datetime import datetime, timedelta
import html
import secrets
import threading
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx
from sqlalchemy.orm import Session

from app.config import CLOUDFLARE_TUNNEL_LOGIN_TTL_SECONDS
from app.models.db import Account, IntegrationConfig, get_db
from app.models.schemas import (
    FacebookPageCandidateResponse,
    FacebookPageConnectResponse,
    FacebookPageSelectRequest,
)
from app.services.cloudflare_tunnel import cloudflare_tunnel_manager

router = APIRouter()

FACEBOOK_SCOPES = [
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
]

_pending_oauth_states: dict[str, dict[str, str]] = {}
_pending_page_selections: dict[str, dict] = {}
_pending_lock = threading.RLock()


def _create_oauth_state(account_id: str, lease_id: str, redirect_uri: str, integration_id: str, facebook_app_id: str) -> str:
    state = secrets.token_urlsafe(32)
    with _pending_lock:
        _pending_oauth_states[state] = {
            "account_id": account_id,
            "lease_id": lease_id,
            "redirect_uri": redirect_uri,
            "integration_id": integration_id,
            "facebook_app_id": facebook_app_id,
        }
    return state


def _consume_oauth_state(state: str) -> dict[str, str] | None:
    with _pending_lock:
        return _pending_oauth_states.pop(state, None)


def _create_selection(account_id: str, lease_id: str, pages: list[dict]) -> str:
    token = secrets.token_urlsafe(32)
    with _pending_lock:
        _pending_page_selections[token] = {
            "account_id": account_id,
            "lease_id": lease_id,
            "pages": pages,
            "expires_at": datetime.utcnow() + timedelta(seconds=CLOUDFLARE_TUNNEL_LOGIN_TTL_SECONDS),
        }
    return token


def _get_selection(token: str) -> dict | None:
    with _pending_lock:
        selection = _pending_page_selections.get(token)
        if not selection:
            return None
        if selection["expires_at"] <= datetime.utcnow():
            _pending_page_selections.pop(token, None)
            return None
        return selection


def _consume_selection(token: str) -> dict | None:
    with _pending_lock:
        selection = _pending_page_selections.pop(token, None)
    if not selection:
        return None
    if selection["expires_at"] <= datetime.utcnow():
        return None
    return selection


def _require_facebook_integration(db: Session) -> IntegrationConfig:
    config = (
        db.query(IntegrationConfig)
        .filter(IntegrationConfig.provider == "meta")
        .first()
    )
    if (
        not config
        or not str(config.facebook_app_id or "").strip()
        or not str(config.facebook_app_secret or "").strip()
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Integração Meta incompleta. Cadastre facebook_app_id e "
                "facebook_app_secret em /integrations/meta antes de iniciar o login da Página."
            ),
        )
    return config


def _get_account_or_404(account_id: str, db: Session) -> Account:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")
    return account


def _get_facebook_account_or_404(account_id: str, db: Session) -> Account:
    account = _get_account_or_404(account_id, db)
    if account.platform != "facebook":
        raise HTTPException(
            status_code=400,
            detail="A conexão de Página Facebook só suporta contas da plataforma Facebook.",
        )
    return account


def _page_can_publish(page: dict) -> bool:
    tasks = page.get("tasks") or []
    return not tasks or "CREATE_CONTENT" in tasks or "MANAGE" in tasks


def _candidate(page: dict) -> FacebookPageCandidateResponse:
    return FacebookPageCandidateResponse(
        id=str(page.get("id")),
        name=str(page.get("name") or page.get("id")),
        tasks=list(page.get("tasks") or []),
        can_publish=_page_can_publish(page),
    )


def _save_page_to_account(account_id: str, page: dict, db: Session) -> FacebookPageConnectResponse:
    account = _get_facebook_account_or_404(account_id, db)
    page_token = page.get("access_token")
    if not page.get("id") or not page_token:
        raise HTTPException(status_code=400, detail="Página selecionada não possui Page Access Token.")

    account.fb_page_id = str(page["id"])
    account.fb_page_token = str(page_token)
    account.fb_page_name = str(page.get("name") or page["id"])
    db.commit()
    db.refresh(account)

    return FacebookPageConnectResponse(
        account_id=account.id,
        facebook_page_connected=True,
        fb_page_id=account.fb_page_id,
        fb_page_name=account.fb_page_name,
        message=f"Página Facebook conectada com sucesso: {account.fb_page_name}.",
    )


def _page_by_id(pages: list[dict], page_id: str) -> dict | None:
    for page in pages:
        if str(page.get("id")) == str(page_id):
            return page
    return None


def _selection_response(account_id: str, lease_id: str, pages: list[dict]) -> FacebookPageConnectResponse:
    selection_token = _create_selection(account_id, lease_id, pages)
    return FacebookPageConnectResponse(
        account_id=account_id,
        facebook_page_connected=False,
        pages=[_candidate(page) for page in pages],
        selection_token=selection_token,
        message="Mais de uma Página encontrada. Selecione uma Página para concluir a conexão.",
    )


def _selection_html(selection_token: str, pages: list[dict]) -> HTMLResponse:
    items = []
    for page in pages:
        page_id = html.escape(str(page.get("id") or ""))
        name = html.escape(str(page.get("name") or page_id))
        tasks = ", ".join(page.get("tasks") or [])
        task_text = html.escape(tasks or "sem tasks informadas")
        href = (
            "/api/auth/facebook-page/select?"
            f"selection_token={urllib.parse.quote(selection_token)}"
            f"&page_id={urllib.parse.quote(page_id)}"
        )
        items.append(
            "<li>"
            f"<strong>{name}</strong><br>"
            f"<code>{page_id}</code><br>"
            f"<small>{task_text}</small><br>"
            f"<a href=\"{href}\">Conectar esta Página</a>"
            "</li>"
        )
    body = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Selecionar Página Facebook</title></head>"
        "<body><h1>Selecionar Página Facebook</h1>"
        "<p>Escolha qual Página deve ser conectada a esta conta do OmniPublisher.</p>"
        f"<ul>{''.join(items)}</ul>"
        "</body></html>"
    )
    return HTMLResponse(body)


async def _exchange_code_for_user_token(
    client: httpx.AsyncClient,
    *,
    config: IntegrationConfig,
    redirect_uri: str,
    code: str,
) -> str:
    token_res = await client.get(
        "https://graph.facebook.com/v22.0/oauth/access_token",
        params={
            "client_id": config.facebook_app_id,
            "redirect_uri": redirect_uri,
            "client_secret": config.facebook_app_secret,
            "code": code,
        },
        timeout=15.0,
    )
    if token_res.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Falha ao obter token Facebook: {token_res.text}")
    short_token = token_res.json().get("access_token")
    if not short_token:
        raise HTTPException(status_code=400, detail=f"Resposta Facebook sem access_token: {token_res.text}")

    long_res = await client.get(
        "https://graph.facebook.com/v22.0/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": config.facebook_app_id,
            "client_secret": config.facebook_app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=15.0,
    )
    if long_res.status_code != 200:
        return short_token
    return long_res.json().get("access_token") or short_token


async def _fetch_pages(client: httpx.AsyncClient, user_token: str) -> list[dict]:
    pages_res = await client.get(
        "https://graph.facebook.com/v22.0/me/accounts",
        params={
            "fields": "id,name,access_token,tasks",
            "access_token": user_token,
        },
        timeout=20.0,
    )
    if pages_res.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Falha ao listar Páginas Facebook: {pages_res.text}")

    pages = []
    for page in pages_res.json().get("data") or []:
        if page.get("id") and page.get("access_token"):
            pages.append(page)
    return pages


@router.get("/login")
def facebook_page_login(account_id: str, db: Session = Depends(get_db)):
    return RedirectResponse(_prepare_facebook_page_login(account_id, db)["auth_url"])


@router.get("/login-info")
def facebook_page_login_info(account_id: str, db: Session = Depends(get_db)):
    """
    Prepara o OAuth e retorna os dados necessários para cadastrar manualmente
    o redirect URI randômico antes de abrir o login no navegador.
    """
    return _prepare_facebook_page_login(account_id, db)


def _prepare_facebook_page_login(account_id: str, db: Session) -> dict:
    _get_facebook_account_or_404(account_id, db)
    config = _require_facebook_integration(db)

    try:
        lease = cloudflare_tunnel_manager.acquire(
            "facebook_page_oauth",
            ttl_seconds=CLOUDFLARE_TUNNEL_LOGIN_TTL_SECONDS,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    redirect_uri = f"{lease.public_url.rstrip('/')}/api/auth/facebook-page/callback"
    state = _create_oauth_state(account_id, lease.lease_id, redirect_uri, config.id, config.facebook_app_id)
    params = {
        "client_id": config.facebook_app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "auth_type": "rerequest",
        "scope": ",".join(FACEBOOK_SCOPES),
    }
    auth_url = f"https://www.facebook.com/v22.0/dialog/oauth?{urllib.parse.urlencode(params)}"
    return {
        "account_id": account_id,
        "provider": "facebook_page",
        "auth_url": auth_url,
        "redirect_uri": redirect_uri,
        "domain": urllib.parse.urlparse(redirect_uri).netloc,
        "scopes": FACEBOOK_SCOPES,
        "expires_in_seconds": CLOUDFLARE_TUNNEL_LOGIN_TTL_SECONDS,
    }


@router.get("/callback")
async def facebook_page_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    error_description: str = Query(None),
    db: Session = Depends(get_db),
):
    pending_state = _consume_oauth_state(state) if state else None
    if error:
        if pending_state:
            cloudflare_tunnel_manager.release(pending_state.get("lease_id"))
        raise HTTPException(status_code=400, detail=f"Erro na autorização: {error_description or error}")
    if not code or not state or not pending_state:
        raise HTTPException(status_code=400, detail="Código de autorização ou state ausente.")

    account_id = pending_state["account_id"]
    lease_id = pending_state["lease_id"]
    redirect_uri = pending_state["redirect_uri"]
    _get_facebook_account_or_404(account_id, db)

    config = (
        db.query(IntegrationConfig)
        .filter(IntegrationConfig.id == pending_state.get("integration_id"))
        .first()
    )
    if not config or not config.facebook_app_id or not config.facebook_app_secret:
        cloudflare_tunnel_manager.release(lease_id)
        raise HTTPException(status_code=409, detail="Integração Meta/Facebook não está mais configurada.")
    if config.facebook_app_id != pending_state.get("facebook_app_id"):
        cloudflare_tunnel_manager.release(lease_id)
        raise HTTPException(status_code=409, detail="O ID do Aplicativo Facebook mudou durante o login.")

    release_delay = 0
    try:
        async with httpx.AsyncClient() as client:
            user_token = await _exchange_code_for_user_token(
                client,
                config=config,
                redirect_uri=redirect_uri,
                code=code,
            )
            pages = await _fetch_pages(client, user_token)

        if not pages:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Nenhuma Página Facebook com Page Access Token foi retornada. "
                    "Verifique permissões pages_show_list, pages_read_engagement e pages_manage_posts."
                ),
            )

        publishable_pages = [page for page in pages if _page_can_publish(page)]
        if len(publishable_pages) == 1:
            result = _save_page_to_account(account_id, publishable_pages[0], db)
            release_delay = 10
            return RedirectResponse(
                f"/#/?facebook_page_success=true&account_id={account_id}"
                f"&fb_page_id={urllib.parse.quote(result.fb_page_id or '')}"
            )

        selectable_pages = publishable_pages or pages
        selection_token = _create_selection(account_id, lease_id, selectable_pages)
        release_delay = CLOUDFLARE_TUNNEL_LOGIN_TTL_SECONDS
        return _selection_html(selection_token, selectable_pages)
    finally:
        cloudflare_tunnel_manager.release(lease_id, delay_seconds=release_delay)


@router.get("/pages", response_model=FacebookPageConnectResponse)
def get_pending_pages(selection_token: str, db: Session = Depends(get_db)):
    selection = _get_selection(selection_token)
    if not selection:
        raise HTTPException(status_code=404, detail="Seleção de Página expirada ou inexistente.")
    return FacebookPageConnectResponse(
        account_id=selection["account_id"],
        facebook_page_connected=False,
        pages=[_candidate(page) for page in selection["pages"]],
        selection_token=selection_token,
        message="Seleção de Página pendente.",
    )


@router.get("/select", response_model=FacebookPageConnectResponse)
def select_page_from_browser(
    selection_token: str,
    page_id: str,
    db: Session = Depends(get_db),
):
    selection = _consume_selection(selection_token)
    if not selection:
        raise HTTPException(status_code=404, detail="Seleção de Página expirada ou inexistente.")
    page = _page_by_id(selection["pages"], page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Página não encontrada na seleção pendente.")
    result = _save_page_to_account(selection["account_id"], page, db)
    cloudflare_tunnel_manager.release(selection.get("lease_id"), delay_seconds=10)
    return result


@router.post("/select", response_model=FacebookPageConnectResponse)
def select_page(payload: FacebookPageSelectRequest, db: Session = Depends(get_db)):
    selection = _consume_selection(payload.selection_token)
    if not selection:
        raise HTTPException(status_code=404, detail="Seleção de Página expirada ou inexistente.")
    page = _page_by_id(selection["pages"], payload.page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Página não encontrada na seleção pendente.")
    result = _save_page_to_account(selection["account_id"], page, db)
    cloudflare_tunnel_manager.release(selection.get("lease_id"), delay_seconds=10)
    return result
