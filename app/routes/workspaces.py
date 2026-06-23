import re
from datetime import datetime
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.db import Account, PublishJob, Workspace, WorkspaceAccount, get_db
from app.models.schemas import (
    WorkspaceAccountAttach,
    WorkspaceAccountResponse,
    WorkspaceAccountsStatusResponse,
    WorkspaceCreate,
    WorkspaceOverviewResponse,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from app.services.account_status_checker import account_status_checker

router = APIRouter()


def _model_dump(model, **kwargs):
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    return model.dict(**kwargs)


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "workspace"


def _get_workspace_or_404(workspace_id: str, db: Session) -> Workspace:
    workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace não encontrado.")
    return workspace


def _ensure_unique_slug(slug: str, db: Session, *, ignore_workspace_id: str | None = None):
    query = db.query(Workspace).filter(Workspace.slug == slug)
    if ignore_workspace_id:
        query = query.filter(Workspace.id != ignore_workspace_id)
    if query.first():
        raise HTTPException(status_code=409, detail=f"Já existe um workspace com slug '{slug}'.")


def _workspace_account_response(row: WorkspaceAccount, account: Account) -> dict:
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "account_id": row.account_id,
        "platform": account.platform,
        "name": account.name,
        "identifier": account.identifier,
        "label": row.label,
        "is_default": row.is_default,
        "created_at": row.created_at,
    }


def _workspace_accounts(workspace_id: str, db: Session) -> list[dict]:
    rows = (
        db.query(WorkspaceAccount, Account)
        .join(Account, WorkspaceAccount.account_id == Account.id)
        .filter(WorkspaceAccount.workspace_id == workspace_id)
        .order_by(Account.platform.asc(), Account.name.asc())
        .all()
    )
    return [_workspace_account_response(row, account) for row, account in rows]


@router.get("/workspaces", response_model=List[WorkspaceResponse])
def list_workspaces(db: Session = Depends(get_db)):
    return db.query(Workspace).order_by(Workspace.created_at.asc()).all()


@router.post("/workspaces", response_model=WorkspaceResponse)
def create_workspace(payload: WorkspaceCreate, db: Session = Depends(get_db)):
    data = _model_dump(payload)
    name = data["name"].strip()
    slug = _slugify(data.get("slug") or name)
    _ensure_unique_slug(slug, db)

    workspace = Workspace(
        name=name,
        slug=slug,
        description=data.get("description"),
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return workspace


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(workspace_id: str, db: Session = Depends(get_db)):
    return _get_workspace_or_404(workspace_id, db)


@router.get("/workspaces/{workspace_id}/overview", response_model=WorkspaceOverviewResponse)
def get_workspace_overview(workspace_id: str, db: Session = Depends(get_db)):
    workspace = _get_workspace_or_404(workspace_id, db)
    accounts = _workspace_accounts(workspace_id, db)
    statuses = account_status_checker.get_workspace_accounts_cached_status(workspace_id)
    counts = {
        "queued": 0,
        "running": 0,
        "success": 0,
        "error": 0,
        "canceled": 0,
    }
    for status, count in (
        db.query(PublishJob.status, func.count(PublishJob.id))
        .filter(PublishJob.workspace_id == workspace_id)
        .group_by(PublishJob.status)
        .all()
    ):
        counts[str(status)] = int(count)

    return {
        "workspace": workspace,
        "accounts": accounts,
        "account_statuses": statuses["accounts"] if statuses else [],
        "task_counts": counts,
    }


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
def update_workspace(
    workspace_id: str,
    updates: WorkspaceUpdate,
    db: Session = Depends(get_db),
):
    workspace = _get_workspace_or_404(workspace_id, db)
    data = _model_dump(updates, exclude_unset=True)

    if "name" in data and data["name"] is not None:
        workspace.name = data["name"].strip()
    if "slug" in data and data["slug"] is not None:
        next_slug = _slugify(data["slug"])
        _ensure_unique_slug(next_slug, db, ignore_workspace_id=workspace_id)
        workspace.slug = next_slug
    if "description" in data:
        workspace.description = data["description"]

    workspace.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(workspace)
    return workspace


@router.delete("/workspaces/{workspace_id}")
def delete_workspace(workspace_id: str, db: Session = Depends(get_db)):
    workspace = _get_workspace_or_404(workspace_id, db)
    db.query(WorkspaceAccount).filter(WorkspaceAccount.workspace_id == workspace_id).delete()
    db.query(PublishJob).filter(PublishJob.workspace_id == workspace_id).update(
        {"workspace_id": None}
    )
    db.delete(workspace)
    db.commit()
    return {
        "success": True,
        "deletedWorkspaceId": workspace_id,
    }


@router.get("/workspaces/{workspace_id}/accounts", response_model=List[WorkspaceAccountResponse])
def list_workspace_accounts(workspace_id: str, db: Session = Depends(get_db)):
    _get_workspace_or_404(workspace_id, db)
    return _workspace_accounts(workspace_id, db)


@router.post("/workspaces/{workspace_id}/accounts", response_model=WorkspaceAccountResponse)
def attach_workspace_account(
    workspace_id: str,
    payload: WorkspaceAccountAttach,
    db: Session = Depends(get_db),
):
    _get_workspace_or_404(workspace_id, db)
    account = db.query(Account).filter(Account.id == payload.account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada.")

    existing = (
        db.query(WorkspaceAccount)
        .filter(WorkspaceAccount.workspace_id == workspace_id)
        .filter(WorkspaceAccount.account_id == payload.account_id)
        .first()
    )
    if existing:
        existing.label = payload.label
        existing.is_default = payload.is_default
        db.commit()
        db.refresh(existing)
        return _workspace_account_response(existing, account)

    association = WorkspaceAccount(
        workspace_id=workspace_id,
        account_id=payload.account_id,
        label=payload.label,
        is_default=payload.is_default,
    )
    db.add(association)
    db.commit()
    db.refresh(association)
    return _workspace_account_response(association, account)


@router.delete("/workspaces/{workspace_id}/accounts/{account_id}")
def detach_workspace_account(
    workspace_id: str,
    account_id: str,
    db: Session = Depends(get_db),
):
    _get_workspace_or_404(workspace_id, db)
    deleted = (
        db.query(WorkspaceAccount)
        .filter(WorkspaceAccount.workspace_id == workspace_id)
        .filter(WorkspaceAccount.account_id == account_id)
        .delete()
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Conta não vinculada ao workspace.")
    db.commit()
    return {
        "success": True,
        "workspaceId": workspace_id,
        "detachedAccountId": account_id,
    }


@router.get("/workspaces/{workspace_id}/accounts/status", response_model=WorkspaceAccountsStatusResponse)
def get_workspace_accounts_status(workspace_id: str, refresh: bool = False):
    result = account_status_checker.check_workspace_accounts_status(
        workspace_id,
        refresh=refresh,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Workspace não encontrado.")
    return result


@router.post("/workspaces/{workspace_id}/accounts/status/refresh", response_model=WorkspaceAccountsStatusResponse)
def refresh_workspace_accounts_status(
    workspace_id: str,
    background_tasks: BackgroundTasks,
):
    result = account_status_checker.mark_workspace_accounts_checking(workspace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Workspace não encontrado.")

    background_tasks.add_task(account_status_checker.refresh_workspace_accounts, workspace_id)
    return result
