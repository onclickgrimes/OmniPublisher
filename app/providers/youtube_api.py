import asyncio
from typing import Dict, Any
# pyrefly: ignore [missing-import]
from googleapiclient.discovery import build
# pyrefly: ignore [missing-import]
from googleapiclient.http import MediaFileUpload

from app.providers.base import BaseProvider
from app.services.session_manager import session_manager
from app.services.task_manager import task_manager

class YouTubeProvider(BaseProvider):
    def __init__(self):
        self.platform_name = "youtube"

    async def upload(self, video_path: str, caption: str, **kwargs) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        account_id = kwargs.get("account_id")
        title = kwargs.get("title", caption[:50] if caption else "Novo vídeo")
        tags = kwargs.get("tags", [])
        privacy = kwargs.get("youtube_privacy", "public")
        
        if not account_id:
            raise ValueError("account_id é obrigatório para o YouTubeProvider.")

        # Obtém credenciais para a conta específica
        creds = session_manager.get_youtube_credentials(account_id)
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": title,
                "description": caption,
                "tags": tags,
                "categoryId": "22"
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = None
        while response is None:
            status, response = await asyncio.to_thread(request.next_chunk)
            
            if status:
                progress = int(status.progress() * 100)
                if task_id:
                    await task_manager.update_status(
                        task_id, self.platform_name, "uploading", progress=progress
                    )

        if "id" in response:
            return {"success": True, "video_id": response["id"], "url": f"https://youtu.be/{response['id']}"}
        else:
            raise Exception("Falha desconhecida no upload do YouTube")

    async def validate_session(self) -> bool:
        # A validação dependeria do account_id agora.
        return True
