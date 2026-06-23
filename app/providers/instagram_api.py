import asyncio
from typing import Dict, Any

from app.providers.base import BaseProvider
from app.services.session_manager import session_manager
from app.services.task_manager import task_manager

class InstagramProvider(BaseProvider):
    def __init__(self):
        self.platform_name = "instagram"

    async def upload(self, video_path: str, caption: str, **kwargs) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        account_id = kwargs.get("account_id")
        instagram_format = kwargs.get("instagram_format", "reels")
        
        if not account_id:
            raise ValueError("account_id é obrigatório para o InstagramProvider.")

        # Obtém o cliente já autenticado com a sessão da conta específica
        cl = session_manager.get_instagram_client(account_id)

        if task_id:
            await task_manager.update_status(
                task_id, self.platform_name, "uploading", progress=10
            )

        def _do_upload():
            if instagram_format == "reels":
                return cl.clip_upload(path=video_path, caption=caption)
            elif instagram_format == "feed":
                return cl.video_upload(path=video_path, caption=caption)
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
                "url": f"https://instagram.com/p/{media.code}/"
            }
        else:
            raise Exception("Falha desconhecida no upload do Instagram")

    async def validate_session(self) -> bool:
        return True
