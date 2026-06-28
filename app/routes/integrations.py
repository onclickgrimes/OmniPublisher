from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.db import IntegrationConfig, get_db
from app.models.schemas import (
    MetaIntegrationCreate,
    MetaIntegrationResponse,
    MetaIntegrationUpdate,
)

router = APIRouter()

META_PROVIDER = "meta"


def _model_dump(model, **kwargs):
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    return model.dict(**kwargs)


def _trim_required(value: str, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"{field_name} é obrigatório.")
    return cleaned


def _get_meta_config(db: Session) -> IntegrationConfig | None:
    return (
        db.query(IntegrationConfig)
        .filter(IntegrationConfig.provider == META_PROVIDER)
        .first()
    )


def _get_meta_config_or_404(db: Session) -> IntegrationConfig:
    config = _get_meta_config(db)
    if not config:
        raise HTTPException(status_code=404, detail="Integração Meta não configurada.")
    return config


def _meta_response(config: IntegrationConfig) -> MetaIntegrationResponse:
    return MetaIntegrationResponse(
        id=config.id,
        provider="meta",
        facebook_app_id=config.facebook_app_id,
        has_facebook_app_secret=bool(config.facebook_app_secret),
        instagram_app_id=config.instagram_app_id,
        has_instagram_app_secret=bool(config.instagram_app_secret),
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@router.post("/integrations/meta", response_model=MetaIntegrationResponse, status_code=201)
def create_meta_integration(payload: MetaIntegrationCreate, db: Session = Depends(get_db)):
    """
    Cadastra as credenciais Meta usadas pelos fluxos Facebook e Instagram.
    Segredos ficam salvos no banco, mas nunca são retornados pela API.
    """
    if _get_meta_config(db):
        raise HTTPException(status_code=409, detail="Integração Meta já configurada.")

    data = _model_dump(payload)
    now = datetime.utcnow()
    config = IntegrationConfig(
        provider=META_PROVIDER,
        facebook_app_id=_trim_required(data["facebook_app_id"], "facebook_app_id"),
        facebook_app_secret=_trim_required(data["facebook_app_secret"], "facebook_app_secret"),
        instagram_app_id=_trim_required(data["instagram_app_id"], "instagram_app_id"),
        instagram_app_secret=_trim_required(data["instagram_app_secret"], "instagram_app_secret"),
        created_at=now,
        updated_at=now,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return _meta_response(config)


@router.put("/integrations/meta", response_model=MetaIntegrationResponse)
def upsert_meta_integration(payload: MetaIntegrationCreate, db: Session = Depends(get_db)):
    """
    Cria ou substitui as credenciais Meta.
    Use antes de iniciar /api/auth/facebook/login.
    """
    data = _model_dump(payload)
    config = _get_meta_config(db)
    now = datetime.utcnow()
    if not config:
        config = IntegrationConfig(
            provider=META_PROVIDER,
            created_at=now,
        )
        db.add(config)

    config.facebook_app_id = _trim_required(data["facebook_app_id"], "facebook_app_id")
    config.facebook_app_secret = _trim_required(data["facebook_app_secret"], "facebook_app_secret")
    config.instagram_app_id = _trim_required(data["instagram_app_id"], "instagram_app_id")
    config.instagram_app_secret = _trim_required(data["instagram_app_secret"], "instagram_app_secret")
    config.updated_at = now
    db.commit()
    db.refresh(config)
    return _meta_response(config)


@router.get("/integrations/meta", response_model=MetaIntegrationResponse)
def get_meta_integration(db: Session = Depends(get_db)):
    """Retorna a configuração Meta sem expor segredos."""
    return _meta_response(_get_meta_config_or_404(db))


@router.patch("/integrations/meta", response_model=MetaIntegrationResponse)
def update_meta_integration(payload: MetaIntegrationUpdate, db: Session = Depends(get_db)):
    """
    Atualiza parcialmente as credenciais do app Meta.
    Envie campos de segredo apenas quando quiser trocar o valor salvo.
    """
    config = _get_meta_config_or_404(db)
    data = _model_dump(payload, exclude_unset=True)
    if "facebook_app_id" in data and data["facebook_app_id"] is not None:
        config.facebook_app_id = _trim_required(data["facebook_app_id"], "facebook_app_id")
    if "facebook_app_secret" in data and data["facebook_app_secret"] is not None:
        config.facebook_app_secret = _trim_required(data["facebook_app_secret"], "facebook_app_secret")
    if "instagram_app_id" in data and data["instagram_app_id"] is not None:
        config.instagram_app_id = _trim_required(data["instagram_app_id"], "instagram_app_id")
    if "instagram_app_secret" in data and data["instagram_app_secret"] is not None:
        config.instagram_app_secret = _trim_required(data["instagram_app_secret"], "instagram_app_secret")
    config.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(config)
    return _meta_response(config)


@router.delete("/integrations/meta")
def delete_meta_integration(db: Session = Depends(get_db)):
    """Remove as credenciais Meta salvas no sidecar."""
    config = _get_meta_config_or_404(db)
    db.delete(config)
    db.commit()
    return {
        "success": True,
        "provider": META_PROVIDER,
    }
