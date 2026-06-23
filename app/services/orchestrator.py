import asyncio
from typing import Dict

from app.models.schemas import PublishRequest
from app.services.task_manager import task_manager
from app.providers.base import BaseProvider
from app.providers.youtube_api import YouTubeProvider
from app.providers.instagram_api import InstagramProvider
from app.providers.tiktok_api import TikTokProvider

class PublishOrchestrator:
    def __init__(self):
        self.providers_map: Dict[str, type[BaseProvider]] = {
            "youtube": YouTubeProvider,
            "instagram": InstagramProvider,
            "tiktok": TikTokProvider,
        }

    async def execute(self, task_id: str, request: PublishRequest):
        tasks = []
        # Itera sobre o dicionário de contas {"plataforma": "account_id"}
        for platform_name, account_id in request.accounts.items():
            provider_class = self.providers_map.get(platform_name.lower())
            
            if not provider_class:
                await task_manager.update_status(
                    task_id, platform_name, "error", error=f"Provider '{platform_name}' não suportado."
                )
                continue

            provider = provider_class()
            tasks.append(self._run_provider(task_id, platform_name, account_id, provider, request))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_provider(self, task_id: str, platform_name: str, account_id: str, provider: BaseProvider, request: PublishRequest):
        try:
            await task_manager.update_status(task_id, platform_name, "uploading", progress=0)

            kwargs = {
                "task_id": task_id,
                "account_id": account_id,
                "title": request.youtube_title,
                "tags": request.youtube_tags,
                "youtube_privacy": request.youtube_privacy,
                "instagram_format": request.instagram_format
            }

            result = await provider.upload(request.video_path, request.caption, **kwargs)
            
            await task_manager.update_status(
                task_id, platform_name, "success", progress=100
            )
            return result

        except Exception as e:
            await task_manager.update_status(
                task_id, platform_name, "error", error=str(e)
            )

orchestrator = PublishOrchestrator()
