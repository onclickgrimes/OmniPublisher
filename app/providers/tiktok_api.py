import sys
import asyncio
from typing import Dict, Any

from app.providers.base import BaseProvider
from app.services.task_manager import task_manager
from app.services.session_manager import session_manager

import concurrent.futures

try:
    # pyrefly: ignore [missing-import]
    import tiktok_uploader.upload as tiktok_up
    TIKTOK_MODULE_LOADED = True
except ImportError:
    TIKTOK_MODULE_LOADED = False

def _run_tiktok_upload(video_path: str, caption: str, session_id: str):
    # pyrefly: ignore [missing-import]
    import tiktok_uploader.upload as tiktok_up
    
    # --- MONKEY PATCH PARA REMOVER MODAIS DO TIKTOK ---
    original_remove_split_window = tiktok_up._remove_split_window
    
    def _patched_remove_split_window(page):
        try:
            # Tenta deletar da tela os overlays que bloqueiam os cliques
            page.evaluate("""
                document.querySelectorAll('.TUXModal-overlay, .react-joyride__overlay, #react-joyride-portal').forEach(el => el.remove());
            """)
        except:
            pass
        return original_remove_split_window(page)
        
    tiktok_up._remove_split_window = _patched_remove_split_window
    # --------------------------------------------------

    cookies_list = [
        {
            "name": "sessionid",
            "value": session_id,
            "domain": ".tiktok.com",
            "path": "/"
        }
    ]
    return tiktok_up.upload_video(video_path, description=caption, cookies_list=cookies_list)


class TikTokProvider(BaseProvider):
    def __init__(self):
        self.platform_name = "tiktok"

    async def upload(self, video_path: str, caption: str, **kwargs) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        account_id = kwargs.get("account_id")

        if not TIKTOK_MODULE_LOADED:
            raise Exception("Módulo tiktok_uploader não encontrado.")

        if not account_id:
            raise ValueError("account_id é obrigatório para o TikTokProvider.")

        # Busca a conta no banco para pegar o session_id
        account = session_manager.get_account(account_id)
        if account.platform != "tiktok":
            raise ValueError("ID informado não pertence a uma conta do TikTok.")
        
        session_id = account.credentials
        if not session_id:
            raise ValueError("Conta do TikTok sem cookie/session_id cadastrado no DB.")

        if task_id:
            await task_manager.update_status(
                task_id, self.platform_name, "uploading", progress=10
            )

        try:
            loop = asyncio.get_running_loop()
            with concurrent.futures.ProcessPoolExecutor() as pool:
                result = await loop.run_in_executor(
                    pool, _run_tiktok_upload, video_path, caption, session_id
                )
        except Exception as e:
            raise Exception(f"Falha ao executar o selenium do TikTok: {str(e)}")

        if task_id:
            await task_manager.update_status(
                task_id, self.platform_name, "uploading", progress=100
            )

        return {"success": True, "message": "Upload do TikTok concluído", "detail": result}

    async def validate_session(self) -> bool:
        return True
