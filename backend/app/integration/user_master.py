"""App User Master — which CRM users are approved to use the planning tool, and which
modules/menus each may access.

Primary store is MySQL (`sc_app_user` + `sc_app_user_menu`). Because the app's MySQL
login is DML-only, the two tables are created once by a DBA via
`backend/db/migrate_user_master.sql`. Until that migration is run — or if MySQL is
unreachable — the module transparently falls back to a JSON file so the feature works
immediately; `import_json_to_db()` then pushes those rows into MySQL once the tables exist.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from . import mysql_db

_JSON = os.getenv("USER_MASTER_STORE") or str(Path(__file__).resolve().parents[2] / "user_master.json")
_LOCK = threading.RLock()

# every newly-approved user gets this password (hashed); admins can reset to it.
DEFAULT_PASSWORD = os.getenv("USER_MASTER_DEFAULT_PASSWORD", "pure@123")
_PBKDF2_ITERS = 200_000


def hash_password(plain: str) -> str:
    """Salted one-way hash: pbkdf2_sha256$iters$salt_b64$hash_b64 (never reversible)."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", (plain or "").encode("utf-8"), salt, _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(plain: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", (plain or "").encode("utf-8"),
                                 base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:   # noqa: BLE001
        return False


def _strip(u: dict) -> dict:
    """Public view of a user — never expose the password hash."""
    return {k: v for k, v in u.items() if k != "password_hash"}


# ── MySQL readiness ───────────────────────────────────────────────────────────
_ready_cache = {"ok": False}


def _db_ready(force=False) -> bool:
    if _ready_cache["ok"] and not force:
        return True
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS c FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name IN ('sc_app_user', 'sc_app_user_menu')")
                _ready_cache["ok"] = (cur.fetchone() or {}).get("c", 0) >= 2
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        _ready_cache["ok"] = False
    return _ready_cache["ok"]


_pwcol_cache = {"ok": None}


def _has_pw_col(force=False) -> bool:
    """True if sc_app_user has the password_hash column (may be absent if the table was
    created by an earlier migration — then password features degrade until the ALTER runs)."""
    if _pwcol_cache["ok"] is not None and not force:
        return _pwcol_cache["ok"]
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM information_schema.columns WHERE "
                            "table_schema=DATABASE() AND table_name='sc_app_user' AND column_name='password_hash'")
                _pwcol_cache["ok"] = (cur.fetchone() or {}).get("c", 0) > 0
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        _pwcol_cache["ok"] = False
    return _pwcol_cache["ok"]


def password_enabled() -> bool:
    """Passwords work when using JSON, or MySQL with the password_hash column present."""
    return (not _db_ready()) or _has_pw_col(force=True)


_tbl_cache: dict = {}


def _has_table(name: str) -> bool:
    if name in _tbl_cache:
        return _tbl_cache[name]
    try:
        conn = mysql_db._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM information_schema.tables WHERE "
                            "table_schema=DATABASE() AND table_name=%s", (name,))
                _tbl_cache[name] = (cur.fetchone() or {}).get("c", 0) > 0
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        _tbl_cache[name] = False
    return _tbl_cache[name]


# ── user access audit log (module grants, role, status, password changes) ─────
def log_access(target_code, target_name, action, detail, actor=None) -> None:
    """Append an audit row: who changed WHAT access for WHICH user, and WHEN. Persists to
    MySQL sc_user_access_log when available, else the JSON store."""
    actor = actor or {}
    by_code = (actor.get("code") or actor.get("user_code") or "")
    by_name = (actor.get("name") or "")
    if _db_ready() and _has_table("sc_user_access_log"):
        try:
            conn = mysql_db._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO sc_user_access_log (logged_at, target_user_code, target_name, "
                        "action, detail, changed_by_code, changed_by_name) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (datetime.now(), (target_code or "")[:32], (target_name or "")[:200],
                         (action or "")[:40], (detail or "")[:400], by_code[:32], by_name[:120]))
                return
            except Exception:   # noqa: BLE001
                pass
            finally:
                conn.close()
        except Exception:   # noqa: BLE001
            pass
    with _LOCK:
        d = _json_load()
        d.setdefault("access_log", []).append(
            {"logged_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
             "target_user_code": target_code or "", "target_name": target_name or "",
             "action": action, "detail": (detail or "")[:400],
             "changed_by_code": by_code, "changed_by_name": by_name})
        _json_save(d)


def get_access_log(user_code=None, limit=300) -> list[dict]:
    cols = ("logged_at", "target_user_code", "target_name", "action", "detail",
            "changed_by_code", "changed_by_name")
    if _db_ready() and _has_table("sc_user_access_log"):
        try:
            conn = mysql_db._connect()
            try:
                with conn.cursor() as cur:
                    q = f"SELECT {', '.join(cols)} FROM sc_user_access_log"
                    params = ()
                    if user_code:
                        q += " WHERE target_user_code=%s"
                        params = (user_code,)
                    q += " ORDER BY id DESC LIMIT %s"
                    cur.execute(q, params + (int(limit),))
                    rows = cur.fetchall()
                    for r in rows:
                        if r.get("logged_at") is not None:
                            r["logged_at"] = str(r["logged_at"])
                    return list(rows)
            finally:
                conn.close()
        except Exception:   # noqa: BLE001
            pass
    rows = _json_load().get("access_log", [])
    if user_code:
        rows = [r for r in rows if r.get("target_user_code") == user_code]
    return list(reversed(rows))[:limit]


def storage_info() -> dict:
    ready = _db_ready(force=True)
    js = _json_load()
    info = {"backend": "mysql" if ready else "json", "db_ready": ready,
            "mysql": mysql_db.status(), "json_users": len(js.get("users", [])),
            "migration": "backend/db/migrate_user_master.sql"}
    if ready:
        try:
            info["db_users"] = len(_mysql_list())
        except Exception:   # noqa: BLE001
            info["db_users"] = None
    return info


# ── JSON fallback ─────────────────────────────────────────────────────────────
def _json_load() -> dict:
    with _LOCK:
        try:
            with open(_JSON, encoding="utf-8") as f:
                d = json.load(f)
            d.setdefault("users", [])
            return d
        except (FileNotFoundError, json.JSONDecodeError):
            return {"users": []}


def _json_save(d) -> None:
    with _LOCK:
        tmp = _JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, default=str)
        os.replace(tmp, _JSON)


def _json_list() -> list[dict]:
    return sorted(_json_load().get("users", []), key=lambda u: (u.get("name") or "").lower())


def _json_add(u, menus, actor) -> dict:
    with _LOCK:
        d = _json_load()
        rec = next((x for x in d["users"] if x["user_code"] == u["user_code"]), None)
        if rec is None:
            rec = {"menus": []}
            d["users"].append(rec)
        rec.update({k: u.get(k, rec.get(k)) for k in
                    ("user_code", "crm_line_id", "name", "username", "email", "mobile",
                     "department", "designation")})
        rec["status"] = rec.get("status") or "active"
        if not rec.get("password_hash"):     # new user -> default password (hashed)
            rec["password_hash"] = u.get("password_hash") or hash_password(DEFAULT_PASSWORD)
        rec["approved_by"] = actor or rec.get("approved_by") or ""
        rec["approved_at"] = rec.get("approved_at") or datetime.now().isoformat(sep=" ", timespec="seconds")
        if menus:
            have = {m["id"] for m in rec["menus"]}
            for m in menus:
                if m.get("id") and m["id"] not in have:
                    rec["menus"].append({"id": m["id"], "label": m.get("label", m["id"])})
        _json_save(d)
        return rec


def _json_remove(user_code) -> None:
    with _LOCK:
        d = _json_load()
        d["users"] = [u for u in d["users"] if u["user_code"] != user_code]
        _json_save(d)


def _json_set_status(user_code, status) -> None:
    with _LOCK:
        d = _json_load()
        for u in d["users"]:
            if u["user_code"] == user_code:
                u["status"] = status
        _json_save(d)


def _json_menu(user_code, menu_id, label, add=True) -> None:
    with _LOCK:
        d = _json_load()
        for u in d["users"]:
            if u["user_code"] == user_code:
                u.setdefault("menus", [])
                u["menus"] = [m for m in u["menus"] if m["id"] != menu_id]
                if add:
                    u["menus"].append({"id": menu_id, "label": label or menu_id})
        _json_save(d)


def _json_set_menus(user_code, menus) -> None:
    with _LOCK:
        d = _json_load()
        for u in d["users"]:
            if u["user_code"] == user_code:
                u["menus"] = [{"id": m["id"], "label": m.get("label", m["id"])} for m in menus if m.get("id")]
        _json_save(d)


def _json_set_password(user_code, password_hash) -> None:
    with _LOCK:
        d = _json_load()
        for u in d["users"]:
            if u["user_code"] == user_code:
                u["password_hash"] = password_hash
        _json_save(d)


# ── MySQL path ────────────────────────────────────────────────────────────────
def _mysql_list() -> list[dict]:
    conn = mysql_db._connect()
    try:
        with conn.cursor() as cur:
            pw = "password_hash, " if _has_pw_col() else ""
            cur.execute(f"SELECT user_code, crm_line_id, name, username, email, mobile, department, "
                        f"designation, status, {pw}approved_by, approved_at "
                        f"FROM sc_app_user ORDER BY name")
            users = {r["user_code"]: {**r, "menus": []} for r in cur.fetchall()}
            cur.execute("SELECT user_code, menu_id, menu_label FROM sc_app_user_menu ORDER BY menu_label")
            for r in cur.fetchall():
                if r["user_code"] in users:
                    users[r["user_code"]]["menus"].append({"id": r["menu_id"], "label": r["menu_label"]})
        for u in users.values():
            if u.get("approved_at"):
                u["approved_at"] = str(u["approved_at"])
            if u.get("crm_line_id") is not None:
                u["crm_line_id"] = int(u["crm_line_id"])
        return list(users.values())
    finally:
        conn.close()


def _mysql_add(u, menus, actor) -> None:
    conn = mysql_db._connect()
    try:
        with conn.cursor() as cur:
            base = ("user_code, crm_line_id, name, username, email, mobile, department, "
                    "designation, status")
            vals = [u["user_code"], u.get("crm_line_id"), u.get("name", "")[:200], (u.get("username") or "")[:100],
                    (u.get("email") or "")[:200], (u.get("mobile") or "")[:40], (u.get("department") or "")[:100],
                    (u.get("designation") or "")[:100]]
            # password_hash set only on INSERT (new user) — re-adding never resets the password
            if _has_pw_col():
                cols = base + ", password_hash, approved_by, approved_at"
                ph = "'active',%s,%s,%s"
                vals += [u.get("password_hash") or hash_password(DEFAULT_PASSWORD), (actor or "")[:120], datetime.now()]
            else:
                cols = base + ", approved_by, approved_at"
                ph = "'active',%s,%s"
                vals += [(actor or "")[:120], datetime.now()]
            cur.execute(
                f"INSERT INTO sc_app_user ({cols}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,{ph}) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), username=VALUES(username), "
                "email=VALUES(email), mobile=VALUES(mobile), department=VALUES(department), "
                "designation=VALUES(designation)", tuple(vals))
            for m in (menus or []):
                if m.get("id"):
                    cur.execute("INSERT IGNORE INTO sc_app_user_menu (user_code, menu_id, menu_label, created_at) "
                                "VALUES (%s,%s,%s,%s)", (u["user_code"], m["id"][:64], (m.get("label") or m["id"])[:120], datetime.now()))
    finally:
        conn.close()


def _mysql_remove(user_code) -> None:
    conn = mysql_db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sc_app_user_menu WHERE user_code=%s", (user_code,))
            cur.execute("DELETE FROM sc_app_user WHERE user_code=%s", (user_code,))
    finally:
        conn.close()


def _mysql_set_status(user_code, status) -> None:
    conn = mysql_db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE sc_app_user SET status=%s WHERE user_code=%s", (status[:16], user_code))
    finally:
        conn.close()


def _mysql_menu(user_code, menu_id, label, add=True) -> None:
    conn = mysql_db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sc_app_user_menu WHERE user_code=%s AND menu_id=%s", (user_code, menu_id))
            if add:
                cur.execute("INSERT INTO sc_app_user_menu (user_code, menu_id, menu_label, created_at) "
                            "VALUES (%s,%s,%s,%s)", (user_code, menu_id[:64], (label or menu_id)[:120], datetime.now()))
    finally:
        conn.close()


def _mysql_set_password(user_code, password_hash) -> None:
    conn = mysql_db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE sc_app_user SET password_hash=%s WHERE user_code=%s",
                        (password_hash, user_code))
    finally:
        conn.close()


def _mysql_set_menus(user_code, menus) -> None:
    conn = mysql_db._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sc_app_user_menu WHERE user_code=%s", (user_code,))
            for m in menus:
                if m.get("id"):
                    cur.execute("INSERT IGNORE INTO sc_app_user_menu (user_code, menu_id, menu_label, created_at) "
                                "VALUES (%s,%s,%s,%s)", (user_code, m["id"][:64], (m.get("label") or m["id"])[:120], datetime.now()))
    finally:
        conn.close()


# ── public API (routes to MySQL when ready, else JSON) ────────────────────────
def _raw_users() -> list[dict]:
    """Users INCLUDING password_hash — internal (auth) use only."""
    if _db_ready():
        try:
            return _mysql_list()
        except Exception:   # noqa: BLE001
            pass
    return _json_list()


def list_users() -> list[dict]:
    """Public list — password hash stripped."""
    return [_strip(u) for u in _raw_users()]


def authenticate(login: str, password: str):
    """Verify a login (username OR user_code) + password. Returns (user_without_hash, None)
    on success, else (None, error_message)."""
    login = (login or "").strip()
    if not login:
        return None, "Enter your username"
    if not password_enabled():
        return None, ("Password login isn't enabled yet — the admin must run the "
                      "password migration (add the password_hash column).")
    for u in _raw_users():
        if u.get("user_code") == login or (u.get("username") or "").lower() == login.lower():
            if (u.get("status") or "active") != "active":
                return None, "This user is disabled — contact the administrator."
            if verify_password(password, u.get("password_hash") or ""):
                user = _strip(u)
                # still on the shared default -> the UI forces a change on first login
                user["must_change_password"] = verify_password(DEFAULT_PASSWORD, u.get("password_hash") or "")
                return user, None
            return None, "Incorrect password."
    return None, "User not found. You must be approved in the User Master first."


def change_password(login: str, current: str, new: str):
    """Self-service change: verify the CURRENT password, then set a new one. Returns
    (user_without_hash, None) or (None, error)."""
    user, err = authenticate(login, current)
    if err:
        return None, err
    new = (new or "").strip()
    if len(new) < 4:
        return None, "New password must be at least 4 characters."
    if new == DEFAULT_PASSWORD:
        return None, "Please choose a password different from the default."
    set_password(user["user_code"], new)
    user["must_change_password"] = False
    return user, None


def set_password(user_code: str, password: str) -> dict:
    if not password_enabled():
        raise ValueError("Password column not present — run the password migration first.")
    ph = hash_password(password)
    (_mysql_set_password if _db_ready() else _json_set_password)(user_code, ph)
    return {"ok": True}


def reset_password(user_code: str) -> dict:
    """Reset the user's password back to the default (pure@123)."""
    if not password_enabled():
        raise ValueError("Password column not present — run the password migration first.")
    ph = hash_password(DEFAULT_PASSWORD)
    (_mysql_set_password if _db_ready() else _json_set_password)(user_code, ph)
    return {"ok": True, "password": DEFAULT_PASSWORD}


def init_missing_passwords() -> dict:
    """Give every user that has no password yet the default (pure@123) — used right after
    the password column is added, for users approved before the feature existed."""
    if not password_enabled():
        raise ValueError("Password column not present — run the password migration first.")
    setter = _mysql_set_password if _db_ready() else _json_set_password
    n = 0
    for u in _raw_users():
        if not (u.get("password_hash") or "").strip():
            setter(u["user_code"], hash_password(DEFAULT_PASSWORD))
            n += 1
    return {"ok": True, "updated": n}


def add_user(u, menus=None, actor="") -> dict:
    if not (u or {}).get("user_code"):
        raise ValueError("user_code is required")
    if _db_ready():
        _mysql_add(u, menus, actor)
    else:
        _json_add(u, menus, actor)
    return {"ok": True}


def remove_user(user_code) -> dict:
    (_mysql_remove if _db_ready() else _json_remove)(user_code)
    return {"ok": True}


def set_status(user_code, status) -> dict:
    (_mysql_set_status if _db_ready() else _json_set_status)(user_code, status)
    return {"ok": True}


def add_menu(user_code, menu_id, label) -> dict:
    if _db_ready():
        _mysql_menu(user_code, menu_id, label, add=True)
    else:
        _json_menu(user_code, menu_id, label, add=True)
    return {"ok": True}


def remove_menu(user_code, menu_id) -> dict:
    if _db_ready():
        _mysql_menu(user_code, menu_id, None, add=False)
    else:
        _json_menu(user_code, menu_id, None, add=False)
    return {"ok": True}


def set_menus(user_code, menus) -> dict:
    (_mysql_set_menus if _db_ready() else _json_set_menus)(user_code, menus or [])
    return {"ok": True}


def import_json_to_db() -> dict:
    """Push the JSON-fallback users + menus into MySQL (idempotent). Requires the tables."""
    if not _db_ready(force=True):
        return {"ok": False, "error": "MySQL tables not created yet — run the migration first.",
                "imported": 0}
    users = _json_load().get("users", [])
    n = 0
    for u in users:
        _mysql_add(u, u.get("menus"), u.get("approved_by", "import"))
        _mysql_set_menus(u["user_code"], u.get("menus", []))
        n += 1
    return {"ok": True, "imported": n}
