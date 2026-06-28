from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.temp_media_store import temp_media_store

router = APIRouter()


@router.get("/api/public-media/{token}")
@router.head("/api/public-media/{token}")
@router.get("/api/public-media/{token}/{filename:path}")
@router.head("/api/public-media/{token}/{filename:path}")
def get_public_media(token: str, filename: str | None = None):
    entry = temp_media_store.get(token)
    if not entry:
        raise HTTPException(status_code=404, detail="Mídia temporária não encontrada ou expirada.")
    return FileResponse(
        entry.path,
        media_type=entry.media_type,
    )
