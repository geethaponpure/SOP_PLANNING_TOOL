"""Database connection helpers (CRM = SQL Server, Oracle = staging).

Connections are built from environment variables (see ``.env.example``); no
credential is ever hardcoded. ``pyodbc`` / ``oracledb`` are imported lazily so
the synthetic path keeps working on machines without the DB drivers installed.
"""
from __future__ import annotations

import os


# --------------------------------------------------------------- CRM (SQL Server)
def _crm_conn_str() -> str:
    driver = os.getenv("CRM_DB_DRIVER", "ODBC Driver 17 for SQL Server")
    server = os.getenv("CRM_DB_SERVER", "10.1.0.146")
    port = os.getenv("CRM_DB_PORT", "1433")
    db = os.getenv("CRM_DB_NAME", "CRMPROD")
    user = os.getenv("CRM_DB_USER", "readuser")
    pwd = os.getenv("CRM_DB_PASSWORD", "")
    trusted = os.getenv("CRM_DB_TRUSTED_CONNECTION", "no").lower() in ("yes", "true", "1")
    parts = [f"DRIVER={{{driver}}}", f"SERVER={server},{port}", f"DATABASE={db}"]
    if trusted:
        parts.append("Trusted_Connection=yes")
    else:
        parts += [f"UID={user}", f"PWD={pwd}"]
    parts.append("TrustServerCertificate=yes")
    return ";".join(parts) + ";"


def get_crm_connection():
    import pyodbc  # lazy
    return pyodbc.connect(_crm_conn_str(), timeout=int(os.getenv("DB_TIMEOUT", "15")))


def _rows_to_dicts(cursor) -> list[dict]:
    cols = [c[0] for c in cursor.description] if cursor.description else []
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def test_connection() -> dict:
    """Quick read-only connectivity check for the CRM (used by /api/health/db)."""
    try:
        rows = crm_query("select @@servername as server, db_name() as db, "
                         "getdate() as server_time")
        info = rows[0] if rows else {}
        return {"ok": True, "server": str(info.get("server")),
                "database": str(info.get("db")), "server_time": str(info.get("server_time")),
                "driver": os.getenv("CRM_DB_DRIVER")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:200]}",
                "driver": os.getenv("CRM_DB_DRIVER"),
                "server": os.getenv("CRM_DB_SERVER"), "database": os.getenv("CRM_DB_NAME")}


def crm_query(sql: str, params: tuple | None = None) -> list[dict]:
    """Run a SELECT (or table-valued function) and return rows as dicts."""
    conn = get_crm_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        return _rows_to_dicts(cur)
    finally:
        conn.close()


def crm_exec_sp(sp_name: str, params: dict | None = None) -> list[dict]:
    """Execute a stored procedure (named params) and return the first result set."""
    conn = get_crm_connection()
    try:
        cur = conn.cursor()
        if params:
            placeholders = ", ".join(f"@{k}=?" for k in params)
            cur.execute(f"EXEC {sp_name} {placeholders}", tuple(params.values()))
        else:
            cur.execute(f"EXEC {sp_name}")
        # skip past any empty result sets the SP may emit first
        while cur.description is None and cur.nextset():
            pass
        return _rows_to_dicts(cur)
    finally:
        conn.close()


# ------------------------------------------------------------- Oracle (staging)
def get_oracle_connection():
    import oracledb  # lazy; thin mode -> no Oracle client install needed
    return oracledb.connect(
        user=os.getenv("ORACLE_USER", ""),
        password=os.getenv("ORACLE_PASSWORD", ""),
        dsn=oracledb.makedsn(
            os.getenv("ORACLE_HOST", ""),
            int(os.getenv("ORACLE_PORT", "1521")),
            service_name=os.getenv("ORACLE_SERVICE", ""),
        ),
    )


def oracle_query(sql: str, params: dict | None = None) -> list[dict]:
    conn = get_oracle_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or {})
        return _rows_to_dicts(cur)
    finally:
        conn.close()
