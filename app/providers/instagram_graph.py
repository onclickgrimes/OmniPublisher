import asyncio
from pathlib import Path
import httpx
from typing import Dict, Any
import urllib.parse

from app.config import CLOUDFLARE_TUNNEL_PUBLISH_TTL_SECONDS
from app.providers.base import BaseProvider
from app.services.cloudflare_tunnel import cloudflare_tunnel_manager
from app.services.session_manager import session_manager
from app.services.task_manager import task_manager
from app.services.temp_media_store import temp_media_store

class InstagramGraphProvider(BaseProvider):
    def __init__(self):
        self.platform_name = "instagram"
        self.api_version = "v22.0"
        self.base_url = f"https://graph.instagram.com/{self.api_version}"

    async def upload(self, video_path: str, caption: str, **kwargs) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        account_id = kwargs.get("account_id")
        instagram_format = kwargs.get("instagram_format", "reels")

        config = session_manager.get_graph_api_config(account_id)
        access_token = config["access_token"]
        ig_business_id = config["ig_business_id"]

        video_file = Path(video_path)
        if not video_file.is_file():
            raise FileNotFoundError(f"Arquivo de vídeo não encontrado: {video_path}")

        if task_id:
            await task_manager.update_status(task_id, self.platform_name, "uploading", progress=5)

        tunnel_lease = None
        temp_media_token = None
        media_type = self._media_type_from_format(instagram_format)

        try:
            if task_id:
                await task_manager.update_status(task_id, self.platform_name, "uploading", progress=10)

            tunnel_lease = await asyncio.to_thread(
                cloudflare_tunnel_manager.acquire,
                "instagram_publish",
                ttl_seconds=CLOUDFLARE_TUNNEL_PUBLISH_TTL_SECONDS,
            )
            temp_media_token = temp_media_store.register(
                video_file,
                ttl_seconds=CLOUDFLARE_TUNNEL_PUBLISH_TTL_SECONDS,
            )
            public_video_url = (
                f"{tunnel_lease.public_url.rstrip('/')}/api/public-media/"
                f"{temp_media_token}/{urllib.parse.quote(video_file.name)}"
            )

            async with httpx.AsyncClient() as client:
                # 1. Criar o container com uma URL temporária assinada. A URL
                # só fica exposta enquanto a publicação está em andamento.
                if task_id:
                    await task_manager.update_status(task_id, self.platform_name, "uploading", progress=20)

                container_data = await self._create_media_container(
                    client,
                    ig_business_id,
                    access_token,
                    caption,
                    media_type,
                    public_video_url,
                )
                container_id = container_data.get("id")
                if not container_id:
                    raise RuntimeError(f"Falha ao criar container de mídia na Graph API. Resposta: {container_data}")

                # 2. Polling até o container ficar pronto
                if task_id:
                    await task_manager.update_status(task_id, self.platform_name, "uploading", progress=55)

                status_code = "IN_PROGRESS"
                status_payload: dict[str, Any] = {"status_code": status_code}
                attempts = 0
                while status_code == "IN_PROGRESS" and attempts < 60:
                    await asyncio.sleep(5)
                    attempts += 1

                    check_res = await client.get(
                        f"{self.base_url}/{container_id}",
                        params={"fields": "status_code,status", "access_token": access_token},
                        timeout=10.0
                    )
                    self._raise_for_graph_error(check_res, "consultar status do container no Instagram")
                    status_payload = check_res.json()
                    status_code = status_payload.get("status_code", "ERROR")

                if status_code != "FINISHED":
                    raise RuntimeError(
                        "Container não processou com sucesso. "
                        f"Container ID: {container_id}. "
                        f"Status final: {status_code}. "
                        f"Detalhe: {status_payload}"
                    )

                # 3. Publicar o Container
                if task_id:
                    await task_manager.update_status(task_id, self.platform_name, "uploading", progress=80)

                publish_params = {
                    "creation_id": container_id,
                    "access_token": access_token
                }
                pub_res = await client.post(
                    f"{self.base_url}/{ig_business_id}/media_publish",
                    params=publish_params,
                    timeout=30.0
                )
                self._raise_for_graph_error(pub_res, "publicar mídia no Instagram")
                publish_data = pub_res.json()
                media_id = publish_data.get("id")
                if not media_id:
                    raise RuntimeError(f"Falha ao publicar mídia no Instagram. Resposta: {publish_data}")
                permalink = await self._get_media_permalink(client, media_id, access_token)

            if task_id:
                await task_manager.update_status(task_id, self.platform_name, "uploading", progress=100)

            return {
                "success": True,
                "media_id": media_id,
                "url": permalink or f"https://instagram.com/p/{media_id}/",
                "engine": "graph_api",
                "upload_type": "temporary_video_url",
                "instagram_media_type": media_type,
                "is_facebook_page_direct": False
            }
        finally:
            temp_media_store.revoke(temp_media_token)
            if tunnel_lease:
                await asyncio.to_thread(cloudflare_tunnel_manager.release, tunnel_lease.lease_id)

    def _media_type_from_format(self, instagram_format: str) -> str:
        if instagram_format == "reels":
            return "REELS"
        if instagram_format == "feed":
            return "REELS"
        raise ValueError("Formato de instagram inválido para Graph API.")

    async def _create_media_container(
        self,
        client: httpx.AsyncClient,
        ig_business_id: str,
        access_token: str,
        caption: str,
        media_type: str,
        public_video_url: str,
    ) -> dict[str, Any]:
        res = await client.post(
            f"{self.base_url}/{ig_business_id}/media",
            data={
                "caption": caption,
                "media_type": media_type,
                "video_url": public_video_url,
                "access_token": access_token,
            },
            timeout=30.0,
        )
        self._raise_for_graph_error(res, "criar container de mídia no Instagram")
        return res.json()

    async def _get_media_permalink(
        self,
        client: httpx.AsyncClient,
        media_id: str,
        access_token: str,
    ) -> str | None:
        res = await client.get(
            f"{self.base_url}/{media_id}",
            params={"fields": "permalink", "access_token": access_token},
            timeout=10.0,
        )
        if res.status_code >= 400:
            return None
        try:
            return res.json().get("permalink")
        except ValueError:
            return None

    def _raise_for_graph_error(self, response: httpx.Response, action: str) -> None:
        if response.status_code < 400:
            return
        detail = response.text
        try:
            payload = response.json()
            detail = str(payload.get("error") or payload.get("debug_info") or payload)
        except ValueError:
            pass
        raise RuntimeError(f"Falha ao {action}: HTTP {response.status_code}. Detalhe: {detail}")

    async def validate_session(self) -> bool:
        return True
