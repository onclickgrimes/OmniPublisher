import asyncio
from pathlib import Path
from typing import Dict, Any

from app.providers.base import BaseProvider
from app.services.session_manager import session_manager
from app.services.task_manager import task_manager


def _thumbnail_from_request(thumb_path: str | None) -> Path | None:
    if thumb_path:
        thumbnail = Path(thumb_path)
        if not thumbnail.is_file():
            raise FileNotFoundError(f"Thumbnail do Instagram não encontrada: {thumb_path}")
        return thumbnail

    raise ValueError(
        "thumb_path é obrigatório para publicar no Instagram neste runtime. "
        "O OmniPublisher não empacota MoviePy/ffmpeg para gerar thumbnail "
        "automaticamente; envie uma imagem de capa em thumb_path."
    )


def _facebook_page_destination_from_profile(client) -> tuple[str | None, str | None, str | None]:
    """
    Usa o page_id exposto pelo endpoint de edição do perfil como fallback para
    contas em que o preflight de Reels não devolve destino de crosspost.
    """
    try:
        result = client.private_request("accounts/current_user/?edit=true&include_reel=true")
    except Exception:
        return None, None, None

    user = result.get("user") if isinstance(result, dict) else {}
    if not isinstance(user, dict):
        return None, None, None

    page_id = user.get("page_id") or user.get("ads_page_id")
    if not page_id:
        return None, None, None

    page_name = user.get("page_name") or user.get("ads_page_name")
    return str(page_id), "PAGE", str(page_name) if page_name else None


def _instagram_facebook_destination_status(client) -> dict[str, Any]:
    """
    Distingue pagina vinculada de crosspost realmente executavel pela sessao.
    O fallback por page_id prova vinculo, mas nao prova permissao para publicar.
    """
    config: dict[str, Any] = {}
    config_error = None
    try:
        config = client.clip_share_to_fb_config() or {}
    except Exception as exc:
        config_error = str(exc)

    confirmed_destination = None
    confirmed_error = None
    try:
        confirmed_destination = client.clip_share_to_fb_destination()
    except Exception as exc:
        confirmed_error = str(exc)

    profile_user: dict[str, Any] = {}
    profile_error = None
    try:
        result = client.private_request("accounts/current_user/?edit=true&include_reel=true")
        user = result.get("user") if isinstance(result, dict) else {}
        if isinstance(user, dict):
            profile_user = user
    except Exception as exc:
        profile_error = str(exc)

    page_id = profile_user.get("page_id") or profile_user.get("ads_page_id")
    page_name = profile_user.get("page_name") or profile_user.get("ads_page_name")

    destination_id = None
    destination_type = None
    source = None
    if confirmed_destination and confirmed_destination.get("destination_id"):
        destination_id = str(confirmed_destination["destination_id"])
        destination_type = str(confirmed_destination.get("destination_type") or "PAGE").upper()
        source = "instagrapi_share_to_fb_config"
    elif page_id:
        destination_id = str(page_id)
        destination_type = "PAGE"
        source = "instagram_profile_page_id"

    can_crosspost_without_fb_token = profile_user.get("can_crosspost_without_fb_token")
    if can_crosspost_without_fb_token is not None:
        can_crosspost_without_fb_token = bool(can_crosspost_without_fb_token)

    share_to_fb_unavailable = config.get("share_to_fb_unavailable")
    if share_to_fb_unavailable is not None:
        share_to_fb_unavailable = bool(share_to_fb_unavailable)

    crosspost_supported = bool(
        destination_id
        and (
            source == "instagrapi_share_to_fb_config"
            or can_crosspost_without_fb_token is True
        )
    )
    requires_facebook_token = bool(destination_id and not crosspost_supported)

    if not destination_id:
        message = "Nenhuma Página Facebook vinculada foi encontrada no perfil Instagram."
    elif crosspost_supported:
        message = "Página vinculada encontrada e crosspost disponível para esta sessão."
    else:
        message = (
            "Página vinculada encontrada, mas a sessão Instagram não pode publicar "
            "nela sem token do Facebook. Para publicação garantida na Página, "
            "cadastre a Página via integração Facebook/Graph API."
        )

    return {
        "available": bool(destination_id),
        "crosspost_supported": crosspost_supported,
        "requires_facebook_token": requires_facebook_token,
        "share_to_fb_unavailable": share_to_fb_unavailable,
        "can_crosspost_without_fb_token": can_crosspost_without_fb_token,
        "destination_id": destination_id,
        "destination_type": destination_type,
        "destination_name": str(page_name) if page_name else None,
        "source": source,
        "message": message,
        "diagnostics": {
            "config_error": config_error,
            "confirmed_error": confirmed_error,
            "profile_error": profile_error,
        },
    }


class InstagramProvider(BaseProvider):
    def __init__(self):
        self.platform_name = "instagram"

    async def upload(self, video_path: str, caption: str, **kwargs) -> Dict[str, Any]:
        account_id = kwargs.get("account_id")
        if not account_id:
            raise ValueError("account_id é obrigatório para o InstagramProvider.")

        use_graph = session_manager.has_graph_api(account_id)
        if use_graph:
            result = await self._upload_via_graph_api(video_path, caption, **kwargs)

            thumb_path = kwargs.get("thumb_path")
            if thumb_path and result.get("media_id") and not result.get("is_facebook_page_direct"):
                # A Graph API do Instagram não suporta thumbnail customizada no upload inicial (usa frame automático).
                # O instagrapi permite editar a capa do Reel/Post pós-upload.
                try:
                    await self._swap_thumbnail_via_instagrapi(account_id, result["media_id"], thumb_path)
                except Exception as e:
                    print(f"Aviso: Upload na Graph API concluiu, mas a troca de capa via Instagrapi falhou: {e}")
            return result
        else:
            return await self._upload_via_instagrapi(video_path, caption, **kwargs)

    async def _upload_via_graph_api(self, video_path: str, caption: str, **kwargs) -> Dict[str, Any]:
        # Para evitar dependência circular, o provider da Graph API será carregado dinamicamente
        # ou invocado através de uma factory/helper.
        from app.providers.instagram_graph import InstagramGraphProvider
        provider = InstagramGraphProvider()
        return await provider.upload(video_path, caption, **kwargs)

    async def _swap_thumbnail_via_instagrapi(self, account_id: str, media_id: str, thumb_path: str):
        thumbnail = _thumbnail_from_request(thumb_path)
        cl = session_manager.get_instagram_client(account_id)

        def _do_swap():
            # Tentar editar a mídia para trocar a thumb.
            # Nota: O endpoint media_edit no instagrapi atual pode não expor `cover_url` diretamente,
            # ou usar um endpoint privado especifico (ex: upload de foto -> associar ao IGTV/Reel).
            # Para esta prova de conceito, caso falhe, é engolido como warning.
            try:
                # O ID retornado pela Graph API muitas vezes não tem a formatação longa do instagrapi (id_userid)
                # cl.media_edit_cover(media_id, thumbnail) - se existir na biblioteca.
                # Como fallback provisório, usaremos _do_swap como placeholder.
                pass
            except Exception as e:
                raise e

        await asyncio.to_thread(_do_swap)

    async def _upload_via_instagrapi(self, video_path: str, caption: str, **kwargs) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        account_id = kwargs.get("account_id")
        instagram_format = kwargs.get("instagram_format", "reels")
        thumb_path = kwargs.get("thumb_path")
        share_to_facebook = bool(kwargs.get("instagram_share_to_facebook"))
        fb_destination_id = kwargs.get("instagram_fb_destination_id")
        fb_destination_type = kwargs.get("instagram_fb_destination_type")

        if share_to_facebook and instagram_format != "reels":
            raise ValueError("Crosspost do Instagram para Facebook via instagrapi está disponível apenas para Reels.")

        thumbnail = _thumbnail_from_request(thumb_path)

        cl = session_manager.get_instagram_client(account_id)

        fb_destination_name = None
        fb_status = None
        if share_to_facebook:
            fb_status = _instagram_facebook_destination_status(cl)
            if not fb_status["available"]:
                raise RuntimeError(f"Crosspost Instagram -> Facebook indisponível: {fb_status['message']}")
            if not fb_status["crosspost_supported"]:
                raise RuntimeError(f"Crosspost Instagram -> Facebook não suportado nesta sessão: {fb_status['message']}")
            fb_destination_id = fb_destination_id or fb_status["destination_id"]
            fb_destination_type = fb_destination_type or fb_status["destination_type"]
            fb_destination_name = fb_status["destination_name"]

        if task_id:
            await task_manager.update_status(
                task_id, self.platform_name, "uploading", progress=10
            )

        def _do_upload():
            if instagram_format == "reels":
                upload_kwargs = {
                    "path": video_path,
                    "caption": caption,
                    "thumbnail": thumbnail,
                    "share_to_facebook": share_to_facebook,
                }
                if fb_destination_id:
                    upload_kwargs["fb_destination_id"] = fb_destination_id
                    upload_kwargs["fb_destination_type"] = fb_destination_type or "PAGE"

                try:
                    return cl.clip_upload(**upload_kwargs)
                except Exception as exc:
                    if share_to_facebook:
                        raise RuntimeError(
                            "Falha ao publicar Reel com crosspost para Facebook via instagrapi. "
                            f"Detalhe: {exc}"
                        ) from exc
                    raise
            elif instagram_format == "feed":
                return cl.video_upload(path=video_path, caption=caption, thumbnail=thumbnail)
            else:
                raise ValueError("Formato de instagram inválido.")

        media = await asyncio.to_thread(_do_upload)

        if task_id:
            await task_manager.update_status(
                task_id, self.platform_name, "uploading", progress=100
            )

        if media and hasattr(media, "id"):
            return {
                "success": True,
                "media_id": media.id,
                "url": f"https://instagram.com/p/{media.code}/",
                "facebook_crosspost_requested": share_to_facebook,
                "facebook_crosspost_supported": fb_status["crosspost_supported"] if fb_status else False,
                "facebook_destination_id": fb_destination_id,
                "facebook_destination_type": fb_destination_type,
                "facebook_destination_name": fb_destination_name,
                "engine": "instagrapi"
            }
        else:
            raise Exception("Falha desconhecida no upload via Instagrapi")

    async def validate_session(self) -> bool:
        return True
