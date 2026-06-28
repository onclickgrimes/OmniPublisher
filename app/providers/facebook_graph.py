import httpx
from pathlib import Path
from typing import Dict, Any

from app.providers.base import BaseProvider
from app.services.session_manager import session_manager
from app.services.task_manager import task_manager

class FacebookPageProvider(BaseProvider):
    def __init__(self):
        self.platform_name = "facebook"
        self.base_url = "https://graph-video.facebook.com/v22.0"

    async def upload(self, video_path: str, caption: str, **kwargs) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        account_id = kwargs.get("account_id")

        if not account_id:
            raise ValueError("account_id é obrigatório para publicar na Página do Facebook.")

        config = session_manager.get_graph_api_config(account_id)
        page_id = config.get("fb_page_id")
        page_token = config.get("fb_page_token")

        if not page_id or not page_token:
            raise ValueError("A conta não possui uma Página do Facebook vinculada ou token de acesso de página (Page Access Token) válido.")

        video_file = Path(video_path)
        if not video_file.is_file():
            raise FileNotFoundError(f"Arquivo de vídeo não encontrado: {video_path}")

        if task_id:
            await task_manager.update_status(task_id, self.platform_name, "uploading", progress=10)

        async with httpx.AsyncClient() as client:
            with open(video_file, "rb") as f:
                data = {
                    "description": caption,
                    "access_token": page_token
                }
                files = {
                    "source": (video_file.name, f, "video/mp4")
                }

                if task_id:
                    await task_manager.update_status(task_id, self.platform_name, "uploading", progress=50)

                res = await client.post(
                    f"{self.base_url}/{page_id}/videos",
                    data=data,
                    files=files,
                    timeout=300.0  # Timeout alto para upload de vídeo
                )

            self._raise_for_graph_error(res, "publicar vídeo na Página Facebook")
            response_data = res.json()
            media_id = response_data.get("id")

            if not media_id:
                raise RuntimeError(f"Falha desconhecida ao fazer upload para a Página Facebook. Resposta: {response_data}")

        if task_id:
            await task_manager.update_status(task_id, self.platform_name, "uploading", progress=100)

        return {
            "success": True,
            "media_id": media_id,
            "url": f"https://www.facebook.com/{page_id}/videos/{media_id}",
            "engine": "graph_api",
            "upload_type": "direct_multipart",
            "facebook_page_id": page_id
        }

    def _raise_for_graph_error(self, response: httpx.Response, action: str) -> None:
        if response.status_code < 400:
            return
        detail = response.text
        try:
            payload = response.json()
            detail = str(payload.get("error") or payload)
        except ValueError:
            pass
        raise RuntimeError(f"Falha ao {action}: HTTP {response.status_code}. Detalhe: {detail}")

    async def validate_session(self) -> bool:
        return True
