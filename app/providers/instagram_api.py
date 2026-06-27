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


class InstagramProvider(BaseProvider):
    def __init__(self):
        self.platform_name = "instagram"

    async def upload(self, video_path: str, caption: str, **kwargs) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        account_id = kwargs.get("account_id")
        instagram_format = kwargs.get("instagram_format", "reels")
        thumb_path = kwargs.get("thumb_path")
        share_to_facebook = bool(kwargs.get("instagram_share_to_facebook"))
        fb_destination_id = kwargs.get("instagram_fb_destination_id")
        fb_destination_type = kwargs.get("instagram_fb_destination_type")
        
        if not account_id:
            raise ValueError("account_id é obrigatório para o InstagramProvider.")

        if share_to_facebook and instagram_format != "reels":
            raise ValueError("Crosspost do Instagram para Facebook está disponível apenas para Reels.")

        thumbnail = _thumbnail_from_request(thumb_path)

        # Obtém o cliente já autenticado com a sessão da conta específica
        cl = session_manager.get_instagram_client(account_id)

        fb_destination_name = None
        if share_to_facebook and not fb_destination_id:
            (
                fb_destination_id,
                fb_destination_type,
                fb_destination_name,
            ) = _facebook_page_destination_from_profile(cl)

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
                            "Falha ao publicar Reel com crosspost para Facebook. "
                            "Verifique se a Página está vinculada no app Instagram ou informe "
                            "instagram_fb_destination_id e instagram_fb_destination_type. "
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
                "facebook_destination_id": fb_destination_id,
                "facebook_destination_type": fb_destination_type,
                "facebook_destination_name": fb_destination_name,
            }
        else:
            raise Exception("Falha desconhecida no upload do Instagram")

    async def validate_session(self) -> bool:
        return True
