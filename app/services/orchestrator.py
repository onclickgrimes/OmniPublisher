import asyncio
from typing import Dict

from app.models.schemas import PublishRequest
from app.services.task_manager import task_manager
from app.providers.base import BaseProvider
from app.providers.youtube_api import YouTubeProvider
from app.providers.instagram_api import InstagramProvider
from app.providers.tiktok_api import TikTokProvider
from app.providers.facebook_graph import FacebookPageProvider


def _validate_provider_result(platform_name: str, result):
    if result is None:
        raise Exception(f"Provider '{platform_name}' não retornou resultado de upload.")

    if isinstance(result, dict) and result.get("success") is False:
        message = result.get("message") or result.get("error") or result.get("detail")
        raise Exception(str(message or f"Provider '{platform_name}' retornou falha."))


def _provider_warnings(result) -> list[dict]:
    if not isinstance(result, dict):
        return []

    warnings = result.get("warnings") or []
    if isinstance(warnings, (str, dict)):
        warnings = [warnings]

    normalized = []
    for warning in warnings:
        if isinstance(warning, dict):
            message = warning.get("message") or warning.get("warning") or warning.get("detail")
            if message:
                normalized.append({**warning, "message": str(message)})
        elif warning:
            normalized.append({"message": str(warning)})
    return normalized


class PublishOrchestrator:
    def __init__(self):
        self.providers_map: Dict[str, type[BaseProvider]] = {
            "youtube": YouTubeProvider,
            "instagram": InstagramProvider,
            "tiktok": TikTokProvider,
            "facebook": FacebookPageProvider,
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
                "instagram_format": request.instagram_format,
                "instagram_share_to_facebook": request.instagram_share_to_facebook,
                "instagram_fb_destination_id": request.instagram_fb_destination_id,
                "instagram_fb_destination_type": request.instagram_fb_destination_type,
                "thumb_path": request.thumb_path,
            }

            result = await provider.upload(request.video_path, request.caption, **kwargs)
            _validate_provider_result(platform_name, result)
            if isinstance(result, dict):
                await task_manager.record_platform_result(task_id, platform_name, result)

            for warning in _provider_warnings(result):
                await task_manager.record_platform_warning(
                    task_id,
                    platform_name,
                    warning["message"],
                    {key: value for key, value in warning.items() if key != "message"},
                )
            
            await task_manager.update_status(
                task_id, platform_name, "success", progress=100
            )
            return result

        except Exception as e:
            await task_manager.update_status(
                task_id, platform_name, "error", error=str(e)
            )

orchestrator = PublishOrchestrator()
