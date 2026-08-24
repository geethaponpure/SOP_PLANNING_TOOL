"""App Role Master — admin-defined roles, stored in MySQL ``sc_app_role`` with a JSON
fallback (same pattern as user_master). Roles feed the per-user role assignment used by
the SRDMS flow / User Master.

Because the app's MySQL login is DML-only, the table is created once by a DBA via
``backend/db/migrate_app_role.sql``. Until then (or if MySQL is unreachable) the module
transparently uses a JSON file so the feature works immediately.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from . import mysql_db

_JSON = os.getenv("APP_ROLES_STORE") or str(Path(__file__).resolve().parents[2] / "app_roles.json")
_LOCK = threading.RLock()

# Seeded into an empty store so the existing role dropdowns keep working out of the box.
DEFAULT_ROLES = ["R&D Requester", "Warehouse In-charge", "Warehouse Executive",
                 "QA / QC", "R&D Head / Plant Head", "System Administrator"]

_ready = {"ok": False}


def _db_ready(force=False) -> bool:
    if _ready["ok"] and not force:
        return True
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM information_schema.tables WHERE "
                            "table_schema=DATABASE() AND table_name='sc_app_role'")
                _ready["ok"] = (cur.fetchone() or {}).get("c", 0) > 0
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        _ready["ok"] = False
    return _ready["ok"]


# ── JSON fallback ─────────────────────────────────────────────────────────────
def _json_load() -> dict:
    with _LOCK:
        try:
            with open(_JSON, encoding="utf-8") as f:
                d = json.load(f)
            d.setdefault("roles", [])
            return d
        except (FileNotFoundError, json.JSONDecodeError):
            return {"roles": []}


def _json_save(d) -> None:
    with _LOCK:
        tmp = _JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, default=str)
        os.replace(tmp, _JSON)


# ── MySQL path ────────────────────────────────────────────────────────────────
def _mysql_list() -> list[dict]:
    conn = mysql_db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT role_name, description, active, created_by, created_at "
                        "FROM sc_app_role ORDER BY role_name")
            rows = cur.fetchall()
            for r in rows:
                r["active"] = bool(r.get("active", 1))
                if r.get("created_at") is not None:
                    r["created_at"] = str(r["created_at"])
            return list(rows)
    finally:
        conn.close()


def _mysql_upsert(name, description, active, actor) -> None:
    conn = mysql_db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sc_app_role (role_name, description, active, created_by, created_at) "
                "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
                "description=VALUES(description), active=VALUES(active)",
                (name[:100], (description or "")[:400], 1 if active else 0, (actor or "")[:120], datetime.now()))
    finally:
        conn.close()


def _mysql_delete(name) -> None:
    conn = mysql_db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sc_app_role WHERE role_name=%s", (name,))
    finally:
        conn.close()


# ── public API ────────────────────────────────────────────────────────────────
def _raw_list() -> list[dict]:
    if _db_ready():
        try:
            return _mysql_list()
        except Exception:   # noqa: BLE001
            pass
    return sorted(_json_load().get("roles", []), key=lambda r: (r.get("role_name") or "").lower())


def list_roles() -> list[dict]:
    """All roles. Seeds the DEFAULT_ROLES the first time the store is empty."""
    rows = _raw_list()
    if not rows:
        for nm in DEFAULT_ROLES:
            try:
                add_role(nm, "", "system-seed")
            except Exception:   # noqa: BLE001
                pass
        rows = _raw_list()
    return rows


def role_names(active_only=True) -> list[str]:
    return [r["role_name"] for r in list_roles() if (r.get("active", True) or not active_only)]


def add_role(name, description="", actor="") -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("Role name is required")
    if _db_ready():
        _mysql_upsert(name, description, True, actor)
    else:
        with _LOCK:
            d = _json_load()
            rec = next((r for r in d["roles"] if r["role_name"].lower() == name.lower()), None)
            if rec is None:
                d["roles"].append({"role_name": name, "description": description or "", "active": True,
                                   "created_by": actor or "",
                                   "created_at": datetime.now().isoformat(sep=" ", timespec="seconds")})
            else:
                rec["description"] = description or rec.get("description", "")
            _json_save(d)
    return {"ok": True}


def update_role(name, description=None, active=None, actor="") -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("Role name is required")
    cur = next((r for r in _raw_list() if r["role_name"].lower() == name.lower()), None)
    desc = description if description is not None else (cur or {}).get("description", "")
    act = active if active is not None else (cur or {}).get("active", True)
    if _db_ready():
        _mysql_upsert(name, desc, act, actor)
    else:
        with _LOCK:
            d = _json_load()
            for r in d["roles"]:
                if r["role_name"].lower() == name.lower():
                    r["description"] = desc
                    r["active"] = bool(act)
            _json_save(d)
    return {"ok": True}


def delete_role(name, actor="") -> dict:
    (_mysql_delete if _db_ready() else _json_delete)(name)
    return {"ok": True}


def _json_delete(name) -> None:
    with _LOCK:
        d = _json_load()
        d["roles"] = [r for r in d["roles"] if r["role_name"].lower() != (name or "").lower()]
        _json_save(d)


def storage_info() -> dict:
    ready = _db_ready(force=True)
    return {"backend": "mysql" if ready else "json", "db_ready": ready,
            "migration": "backend/db/migrate_app_role.sql",
            "json_roles": len(_json_load().get("roles", []))}


def import_json_to_db() -> dict:
    """Push JSON-fallback roles into MySQL once the table exists (idempotent)."""
    if not _db_ready(force=True):
        return {"ok": False, "error": "sc_app_role not created yet — run the migration first.", "imported": 0}
    n = 0
    for r in _json_load().get("roles", []):
        _mysql_upsert(r["role_name"], r.get("description", ""), r.get("active", True), r.get("created_by", "import"))
        n += 1
    return {"ok": True, "imported": n}
