import sys
import asyncio
import logging
import contextlib
import io
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

class _ListLogHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record):
        try:
            message = self.format(record)
        except Exception:
            message = str(record.getMessage())
        if message:
            self.messages.append(message)


def _last_relevant_log_message(messages: list[str]) -> str:
    for message in reversed(messages):
        cleaned = str(message or "").strip()
        if cleaned and not cleaned.startswith("Failed to upload "):
            return cleaned
    return ""


def _split_diagnostic_lines(*values: str) -> list[str]:
    lines: list[str] = []
    for value in values:
        for line in str(value or "").splitlines():
            cleaned = line.strip()
            if cleaned:
                lines.append(cleaned)
    return lines


def _remove_tiktok_upload_overlays(page):
    try:
        page.evaluate("""
            document
                .querySelectorAll('.TUXModal-overlay, .react-joyride__overlay, #react-joyride-portal, [data-test-id="overlay"]')
                .forEach(el => el.remove());
        """)
    except Exception:
        pass


def _run_tiktok_upload(video_path: str, caption: str, session_id: str):
    # pyrefly: ignore [missing-import]
    import tiktok_uploader.upload as tiktok_up
    # pyrefly: ignore [missing-import]
    from tiktok_uploader import config
    
    # --- MONKEY PATCH PARA REMOVER MODAIS DO TIKTOK ---
    original_remove_split_window = tiktok_up._remove_split_window
    
    def _patched_remove_split_window(page):
        _remove_tiktok_upload_overlays(page)
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

    log_handler = _ListLogHandler()
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    tiktok_up.logger.addHandler(log_handler)
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    previous_quit_on_end = config.quit_on_end
    config.quit_on_end = True

    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            failed_uploads = tiktok_up.upload_video(
                video_path,
                description=caption,
                cookies_list=cookies_list,
            )
    except Exception as exc:
        diagnostics = _split_diagnostic_lines(
            "\n".join(log_handler.messages),
            stdout_buffer.getvalue(),
            stderr_buffer.getvalue(),
        )
        return {
            "failed": [
                {
                    "path": video_path,
                    "error": str(exc),
                }
            ],
            "logs": diagnostics,
        }
    finally:
        config.quit_on_end = previous_quit_on_end
        tiktok_up._remove_split_window = original_remove_split_window
        tiktok_up.logger.removeHandler(log_handler)

    diagnostics = _split_diagnostic_lines(
        "\n".join(log_handler.messages),
        stdout_buffer.getvalue(),
        stderr_buffer.getvalue(),
    )
    failure_reason = _last_relevant_log_message(diagnostics)
    enriched_failed_uploads = []
    for item in failed_uploads or []:
        if isinstance(item, dict):
            enriched = dict(item)
            if failure_reason and not any(enriched.get(key) for key in ["error", "message", "reason"]):
                enriched["error"] = failure_reason
            enriched_failed_uploads.append(enriched)
        else:
            enriched_failed_uploads.append(
                {
                    "path": video_path,
                    "error": failure_reason or str(item),
                    "detail": item,
                }
            )

    return {
        "failed": enriched_failed_uploads,
        "logs": diagnostics,
    }


def _normalize_tiktok_upload_result(result) -> dict[str, Any]:
    if isinstance(result, dict) and "failed" in result:
        return {
            "failed": result.get("failed") or [],
            "logs": result.get("logs") or [],
        }
    if isinstance(result, list):
        return {
            "failed": result,
            "logs": [],
        }
    return {
        "failed": None,
        "logs": [],
    }


def _describe_failed_uploads(failed_uploads, logs: list[str] | None = None) -> str:
    if not failed_uploads:
        return ""

    descriptions = []
    for item in failed_uploads:
        if isinstance(item, dict):
            path = item.get("path") or item.get("filename") or "video"
            error = item.get("error") or item.get("message") or item.get("reason")
            descriptions.append(
                f"{path}: {error}" if error else f"{path}: falha sem detalhe retornado pela biblioteca"
            )
        else:
            descriptions.append(str(item))
    detail = "; ".join(descriptions)
    log_reason = _last_relevant_log_message(logs or [])
    if log_reason and log_reason not in detail:
        return f"{detail}; {log_reason}"
    return detail


def _validate_tiktok_upload_result(result) -> list[dict[str, Any]]:
    """
    tiktok_uploader.upload_video retorna lista de vídeos que falharam.
    Lista vazia significa sucesso; lista com itens significa falha.
    """
    if result is None:
        raise Exception("TikTok não retornou confirmação de upload.")

    normalized = _normalize_tiktok_upload_result(result)
    failed_uploads = normalized["failed"]
    logs = normalized["logs"]

    if isinstance(failed_uploads, list):
        if not failed_uploads:
            return []
        detail = _describe_failed_uploads(failed_uploads, logs)
        raise Exception(f"Upload do TikTok falhou: {detail or failed_uploads}")

    raise Exception(f"Retorno inesperado do TikTok uploader: {result!r}")


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

        warnings = _validate_tiktok_upload_result(result)

        if task_id:
            await task_manager.update_status(
                task_id, self.platform_name, "uploading", progress=95
            )

        return {
            "success": True,
            "message": "Upload do TikTok concluído",
            "detail": result,
            "warnings": warnings,
        }

    async def validate_session(self) -> bool:
        return True
