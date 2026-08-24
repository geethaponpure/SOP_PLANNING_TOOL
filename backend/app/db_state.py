"""SQLite persistence for workflow state (blueprint Section 8).

Confirmations, the consensus lock and the append-only audit trail are persisted
per cycle so they survive a restart -- the system of record is durable, not just
in-memory. Pure stdlib (sqlite3); the DB file path is configurable via STATE_DB.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

_DB = os.getenv("STATE_DB") or str(Path(__file__).resolve().parent.parent / "sop_state.db")
_LOCK = threading.Lock()


def _conn():
    c = sqlite3.connect(_DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with _LOCK, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS confirmations(
            cycle TEXT, sku TEXT, data TEXT, PRIMARY KEY(cycle, sku));
        CREATE TABLE IF NOT EXISTS lock_state(
            cycle TEXT PRIMARY KEY, data TEXT);
        CREATE TABLE IF NOT EXISTS consensus(
            cycle TEXT, sku TEXT, quantity REAL, PRIMARY KEY(cycle, sku));
        CREATE TABLE IF NOT EXISTS audit(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, cycle TEXT, entry TEXT);
        """)


def save_confirmation(cycle: str, sku: str, conf: dict) -> None:
    with _LOCK, _conn() as c:
        c.execute("INSERT OR REPLACE INTO confirmations(cycle, sku, data) VALUES(?,?,?)",
                  (cycle, sku, json.dumps(conf)))


def load_confirmations(cycle: str) -> dict[str, dict]:
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT sku, data FROM confirmations WHERE cycle=?", (cycle,)).fetchall()
    return {r["sku"]: json.loads(r["data"]) for r in rows}


def set_lock(cycle: str, meta: dict, consensus: dict[str, float]) -> None:
    with _LOCK, _conn() as c:
        c.execute("INSERT OR REPLACE INTO lock_state(cycle, data) VALUES(?,?)",
                  (cycle, json.dumps(meta)))
        c.executemany("INSERT OR REPLACE INTO consensus(cycle, sku, quantity) VALUES(?,?,?)",
                      [(cycle, s, q) for s, q in consensus.items()])


def clear_lock(cycle: str) -> None:
    with _LOCK, _conn() as c:
        c.execute("DELETE FROM lock_state WHERE cycle=?", (cycle,))
        c.execute("DELETE FROM consensus WHERE cycle=?", (cycle,))


def load_lock(cycle: str):
    with _LOCK, _conn() as c:
        row = c.execute("SELECT data FROM lock_state WHERE cycle=?", (cycle,)).fetchone()
        cons = c.execute("SELECT sku, quantity FROM consensus WHERE cycle=?", (cycle,)).fetchall()
    meta = json.loads(row["data"]) if row else None
    consensus = {r["sku"]: r["quantity"] for r in cons}
    return meta, consensus


def append_audit(cycle: str, entry: dict) -> None:
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO audit(cycle, entry) VALUES(?,?)", (cycle, json.dumps(entry)))


def load_audit(cycle: str) -> list[dict]:
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT entry FROM audit WHERE cycle=? ORDER BY seq", (cycle,)).fetchall()
    return [json.loads(r["entry"]) for r in rows]


def clear_cycle(cycle: str) -> None:
    with _LOCK, _conn() as c:
        for t in ("confirmations", "lock_state", "consensus", "audit"):
            c.execute(f"DELETE FROM {t} WHERE cycle=?", (cycle,))
