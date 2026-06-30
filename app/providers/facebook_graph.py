# pyrefly: ignore [missing-import]
import httpx
from pathlib import Path
from typing import Dict, Any

from app.providers.base import BaseProvider
from app.services.session_manager import session_manager
from app.services.task_manager import task_manager

DIRECT_UPLOAD_MAX_BYTES = 50 * 1024 * 1024

class FacebookPageProvider(BaseProvider):
    def __init__(self):
        self.platform_name = "facebook"
        self.base_url = "https://graph-video.facebook.com/v22.0"

    async def upload(self, video_path: str, caption: str, **kwargs) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        account_id = kwargs.get("account_id")

        if not account_id:
            raise ValueError("account_id é obrigatório para publicar na Página do Facebook.")

        config = session_manager.get_facebook_page_config(account_id)
        page_id = config.get("fb_page_id")
        page_token = config.get("fb_page_token")

        video_file = Path(video_path)
        if not video_file.is_file():
            raise FileNotFoundError(f"Arquivo de vídeo não encontrado: {video_path}")

        file_size = video_file.stat().st_size
        if task_id:
            await task_manager.update_status(task_id, self.platform_name, "uploading", progress=10)

        async with httpx.AsyncClient() as client:
            if file_size > DIRECT_UPLOAD_MAX_BYTES:
                result = await self._upload_resumable(
                    client,
                    video_file,
                    file_size=file_size,
                    page_id=page_id,
                    page_token=page_token,
                    caption=caption,
                    task_id=task_id,
                )
                if task_id:
                    await task_manager.update_status(task_id, self.platform_name, "uploading", progress=100)
                return result

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

    async def _upload_resumable(
        self,
        client: httpx.AsyncClient,
        video_file: Path,
        *,
        file_size: int,
        page_id: str,
        page_token: str,
        caption: str,
        task_id: str | None,
    ) -> Dict[str, Any]:
        start_res = await client.post(
            f"{self.base_url}/{page_id}/videos",
            data={
                "upload_phase": "start",
                "file_size": str(file_size),
                "access_token": page_token,
            },
            timeout=30.0,
        )
        self._raise_for_graph_error(start_res, "iniciar upload resumível na Página Facebook")
        session_data = start_res.json()
        upload_session_id = session_data.get("upload_session_id")
        media_id = session_data.get("video_id")
        start_offset = int(session_data.get("start_offset", 0))
        end_offset = int(session_data.get("end_offset", 0))

        if not upload_session_id or not media_id:
            raise RuntimeError(f"Falha ao iniciar upload resumível na Página Facebook. Resposta: {session_data}")

        if task_id:
            await task_manager.update_status(task_id, self.platform_name, "uploading", progress=15)

        with open(video_file, "rb") as f:
            while start_offset < end_offset:
                chunk_size = end_offset - start_offset
                f.seek(start_offset)
                chunk = f.read(chunk_size)
                if not chunk:
                    raise RuntimeError(
                        "Falha ao ler chunk do vídeo para upload resumível. "
                        f"Offset: {start_offset}, tamanho solicitado: {chunk_size}."
                    )

                transfer_res = await client.post(
                    f"{self.base_url}/{page_id}/videos",
                    data={
                        "upload_phase": "transfer",
                        "upload_session_id": upload_session_id,
                        "start_offset": str(start_offset),
                        "access_token": page_token,
                    },
                    files={
                        "video_file_chunk": (video_file.name, chunk, "application/octet-stream"),
                    },
                    timeout=300.0,
                )
                self._raise_for_graph_error(transfer_res, "enviar chunk do vídeo para a Página Facebook")
                transfer_data = transfer_res.json()
                next_start_offset = int(transfer_data.get("start_offset", start_offset))
                next_end_offset = int(transfer_data.get("end_offset", end_offset))

                if next_start_offset <= start_offset and next_end_offset <= end_offset:
                    raise RuntimeError(
                        "Upload resumível do Facebook não avançou. "
                        f"Resposta: {transfer_data}"
                    )

                start_offset = next_start_offset
                end_offset = next_end_offset

                if task_id:
                    transferred_ratio = min(max(start_offset / max(file_size, 1), 0), 1)
                    progress = 15 + int(transferred_ratio * 75)
                    await task_manager.update_status(
                        task_id,
                        self.platform_name,
                        "uploading",
                        progress=min(progress, 90),
                    )

        finish_res = await client.post(
            f"{self.base_url}/{page_id}/videos",
            data={
                "upload_phase": "finish",
                "upload_session_id": upload_session_id,
                "description": caption,
                "access_token": page_token,
            },
            timeout=60.0,
        )
        self._raise_for_graph_error(finish_res, "finalizar upload resumível na Página Facebook")
        finish_data = finish_res.json()
        if finish_data.get("success") is False:
            raise RuntimeError(f"Facebook recusou a finalização do upload resumível. Resposta: {finish_data}")

        return {
            "success": True,
            "media_id": media_id,
            "url": f"https://www.facebook.com/{page_id}/videos/{media_id}",
            "engine": "graph_api",
            "upload_type": "resumable",
            "facebook_page_id": page_id,
            "file_size": file_size,
        }

    def _raise_for_graph_error(self, response: httpx.Response, action: str) -> None:
        if response.status_code < 400:
            return
        detail = response.text.strip()
        try:
            payload = response.json()
            detail = str(payload.get("error") or payload)
        except ValueError:
            pass
        if not detail:
            detail = f"Resposta sem corpo. Headers: {dict(response.headers)}"
        raise RuntimeError(f"Falha ao {action}: HTTP {response.status_code}. Detalhe: {detail}")

    async def validate_session(self) -> bool:
        return True
