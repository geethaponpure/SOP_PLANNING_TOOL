"""User Master (admin) REST API — approve CRM users for the planning tool and grant
per-user module/menu access.

Only CRM users in the admin-configured departments (supply-chain / planning / manufacturing
/ warehouse / R&D) are offered. Approved users + their menu grants persist in MySQL
(sc_app_user / sc_app_user_menu), with a JSON fallback until the migration is run.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..integration import crm_sources as _crm
from ..integration import planning_settings as _ps
from ..integration import user_master as _um

router = APIRouter(prefix="/api/user-master", tags=["user-master"])


def _live() -> bool:
    return os.getenv("DATA_SOURCE", "synthetic").lower() == "live"


def _act(actor_code: str, actor_name: str) -> dict:
    return {"code": actor_code or "", "name": actor_name or ""}


def _uname(code: str) -> str:
    for u in _um.list_users():
        if u.get("user_code") == code:
            return u.get("name", "")
    return ""


class Menu(BaseModel):
    id: str
    label: str = ""


class UserIn(BaseModel):
    user_code: str
    crm_line_id: int | None = None
    name: str = ""
    username: str = ""
    email: str = ""
    mobile: str = ""
    department: str = ""
    designation: str = ""
    menus: list[Menu] = []
    actor: str = ""


class StatusIn(BaseModel):
    status: str = "active"


class AvatarIn(BaseModel):
    avatar: str = ""


class MenuIn(BaseModel):
    menu_id: str
    menu_label: str = ""


class MenusIn(BaseModel):
    menus: list[Menu] = []


class DeptIn(BaseModel):
    departments: list[str] = []


class LoginIn(BaseModel):
    login: str = ""          # username or user_code
    password: str = ""


class PasswordIn(BaseModel):
    password: str = ""


class ChangePwIn(BaseModel):
    login: str = ""
    current_password: str = ""
    new_password: str = ""


# ── CRM user picker ───────────────────────────────────────────────────────────
@router.get("/crm-users")
def crm_users(q: str | None = None, all_departments: bool = False):
    if not _live():
        return {"users": [], "note": "Requires DATA_SOURCE=live to read CRM users."}
    s = _ps.load()
    depts = None if all_departments else s.get("app_allowed_departments", [])
    try:
        rows = _crm.crm_users(departments=depts, q=q)
    except Exception as e:   # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CRM read failed: {str(e).splitlines()[0][:160]}")
    approved = {u["user_code"] for u in _um.list_users()}
    # coerce NULLs to "" so the add-user form (string fields) never receives null
    return {"users": [{"user_code": r.get("UserCode") or "", "line_id": r.get("LineId"),
                       "name": r.get("Name") or "", "username": r.get("Username") or "",
                       "email": r.get("Email") or "", "mobile": r.get("Mobile") or "",
                       "department": r.get("Department") or "", "designation": r.get("Designation") or "",
                       "already_added": r.get("UserCode") in approved} for r in rows],
            "allowed_departments": s.get("app_allowed_departments", [])}


@router.get("/departments")
def departments():
    s = _ps.load()
    out = {"allowed": s.get("app_allowed_departments", []), "all": []}
    if _live():
        try:
            out["all"] = [{"department": r.get("Department"), "n": r.get("N")}
                          for r in _crm.crm_user_departments()]
        except Exception:   # noqa: BLE001
            pass
    return out


@router.post("/allowed-departments")
def set_allowed_departments(body: DeptIn):
    s = _ps.save({"app_allowed_departments": [d for d in body.departments if d and d.strip()]})
    return {"allowed_departments": s.get("app_allowed_departments", [])}


# ── approved app users ────────────────────────────────────────────────────────
@router.get("/users")
def list_users():
    return {"users": _um.list_users()}


@router.get("/status")
def status():
    info = _um.storage_info()
    info["user_count"] = len(_um.list_users())      # 0 => app runs open (bootstrap)
    info["default_password"] = _um.DEFAULT_PASSWORD
    info["password_enabled"] = _um.password_enabled()
    # Auto-backfill: any approved user with no password gets the default (pure@123)
    # automatically — no manual "Initialize default passwords" step needed. New users
    # already get it on creation; this catches legacy/imported users on app load.
    if info["password_enabled"]:
        try:
            missing = any(not (u.get("password_hash") or "").strip() for u in _um._raw_users())
            if missing:
                _um.init_missing_passwords()
        except Exception:   # noqa: BLE001 — never block status on a backfill hiccup
            pass
    return info


# ── login / logout / password ─────────────────────────────────────────────────
@router.post("/login")
def login(body: LoginIn):
    user, err = _um.authenticate(body.login, body.password)
    if err:
        raise HTTPException(status_code=401, detail=err)
    return {"ok": True, "user": user}


@router.post("/change-password")
def change_password(body: ChangePwIn):
    user, err = _um.change_password(body.login, body.current_password, body.new_password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True, "user": user}


@router.post("/users/{user_code}/set-password")
def set_password(user_code: str, body: PasswordIn, actor_code: str = "", actor_name: str = ""):
    if len((body.password or "").strip()) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    try:
        _um.set_password(user_code, body.password.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _um.log_access(user_code, _uname(user_code), "password_set", "password changed", _act(actor_code, actor_name))
    return {"ok": True}


@router.post("/users/{user_code}/reset-password")
def reset_password(user_code: str, actor_code: str = "", actor_name: str = ""):
    try:
        r = _um.reset_password(user_code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _um.log_access(user_code, _uname(user_code), "password_reset", "reset to default", _act(actor_code, actor_name))
    return r


@router.post("/init-passwords")
def init_passwords():
    try:
        return _um.init_missing_passwords()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/import")
def import_json():
    return _um.import_json_to_db()


@router.post("/users")
def add_user(body: UserIn, actor_code: str = "", actor_name: str = ""):
    try:
        u = body.model_dump(exclude={"menus", "actor"})
        _um.add_user(u, [m.model_dump() for m in body.menus], body.actor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    mods = ", ".join(m.id for m in body.menus) or "no modules"
    _um.log_access(body.user_code, body.name, "approved", f"approved user · modules: {mods}",
                   _act(actor_code, actor_name or body.actor))
    return {"users": _um.list_users()}


@router.delete("/users/{user_code}")
def remove_user(user_code: str, actor_code: str = "", actor_name: str = ""):
    name = _uname(user_code)
    _um.remove_user(user_code)
    _um.log_access(user_code, name, "removed", "removed from the app", _act(actor_code, actor_name))
    return {"users": _um.list_users()}


@router.post("/users/{user_code}/status")
def set_status(user_code: str, body: StatusIn, actor_code: str = "", actor_name: str = ""):
    _um.set_status(user_code, body.status)
    _um.log_access(user_code, _uname(user_code), "status", f"status → {body.status}", _act(actor_code, actor_name))
    return {"users": _um.list_users()}


@router.post("/users/{user_code}/avatar")
def set_avatar(user_code: str, body: AvatarIn, actor_code: str = "", actor_name: str = ""):
    _um.set_avatar(user_code, body.avatar)
    _um.log_access(user_code, _uname(user_code), "avatar",
                   f"avatar → {body.avatar or 'cleared'}", _act(actor_code, actor_name))
    return {"users": _um.list_users()}


@router.post("/users/{user_code}/menus/add")
def add_menu(user_code: str, body: MenuIn, actor_code: str = "", actor_name: str = ""):
    _um.add_menu(user_code, body.menu_id, body.menu_label)
    _um.log_access(user_code, _uname(user_code), "grant", f"granted module: {body.menu_label or body.menu_id}", _act(actor_code, actor_name))
    return {"users": _um.list_users()}


@router.post("/users/{user_code}/menus/remove")
def remove_menu(user_code: str, body: MenuIn, actor_code: str = "", actor_name: str = ""):
    _um.remove_menu(user_code, body.menu_id)
    _um.log_access(user_code, _uname(user_code), "revoke", f"revoked module: {body.menu_id}", _act(actor_code, actor_name))
    return {"users": _um.list_users()}


@router.put("/users/{user_code}/menus")
def set_menus(user_code: str, body: MenusIn, actor_code: str = "", actor_name: str = ""):
    _um.set_menus(user_code, [m.model_dump() for m in body.menus])
    mods = ", ".join(m.id for m in body.menus) or "none"
    _um.log_access(user_code, _uname(user_code), "set_modules", f"modules set to: {mods}", _act(actor_code, actor_name))
    return {"users": _um.list_users()}


@router.get("/access-log")
def access_log(user_code: str | None = None, limit: int = 300):
    return {"log": _um.get_access_log(user_code, limit)}
