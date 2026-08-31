"""Role Master (admin) REST API — create / edit / delete the roles that can be assigned
to users. Persists in MySQL ``sc_app_role`` with a JSON fallback (see app_roles.py)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..integration import app_roles as _roles
from ..integration import user_master as _um   # reuse the access-audit log

router = APIRouter(prefix="/api/roles", tags=["roles"])


def _act(actor_code: str, actor_name: str) -> dict:
    return {"code": actor_code or "", "name": actor_name or ""}


class RoleIn(BaseModel):
    role_name: str
    description: str = ""
    active: bool = True


@router.get("")
def list_roles():
    return {"roles": _roles.list_roles(), "storage": _roles.storage_info()}


@router.post("")
def add_role(body: RoleIn, actor_code: str = "", actor_name: str = ""):
    try:
        _roles.add_role(body.role_name, body.description, actor_name or actor_code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _um.log_access("", "", "role_create", f"Role created: {body.role_name}", _act(actor_code, actor_name))
    return {"roles": _roles.list_roles()}


@router.put("")
def update_role(body: RoleIn, actor_code: str = "", actor_name: str = ""):
    try:
        _roles.update_role(body.role_name, body.description, body.active, actor_name or actor_code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _um.log_access("", "", "role_update",
                   f"Role updated: {body.role_name} ({'active' if body.active else 'disabled'})",
                   _act(actor_code, actor_name))
    return {"roles": _roles.list_roles()}


@router.delete("")
def delete_role(role_name: str, actor_code: str = "", actor_name: str = ""):
    _roles.delete_role(role_name, actor_name or actor_code)
    _um.log_access("", "", "role_delete", f"Role deleted: {role_name}", _act(actor_code, actor_name))
    return {"roles": _roles.list_roles()}


@router.post("/import")
def import_json():
    return _roles.import_json_to_db()
