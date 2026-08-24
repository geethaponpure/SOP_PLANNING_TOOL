"""SRDMS — Sample Request & Dispatch Management System — persistence layer.

R&D raises sample-material requests to a plant warehouse; the warehouse dispatches
(batch + delivery mode), holds, rejects or short-closes each line; the requester
acknowledges receipt. The whole lifecycle is timestamped for TAT / ageing reports and
drives an email-notification outbox (matrix N1-N13 in the BRD).

Stored as a single JSON document (SRDMS_STORE, default backend/srdms_store.json) — the
same file-persistence approach as planning_settings, so it works with zero database DDL
(the app's MySQL user is DML-only). A process-level lock guards read-modify-write.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

_PATH = os.getenv("SRDMS_STORE") or str(Path(__file__).resolve().parents[2] / "srdms_store.json")
_LOCK = threading.RLock()

# ── master defaults (admin-editable via the Masters page) ─────────────────────
DEFAULT_MASTERS = {
    # plant -> its warehouse fulfilment owners. Emails drive the notification matrix.
    "plants": [
        {"id": "PUZHAL", "name": "Puzhal Plant",
         "incharge_name": "", "incharge_email": "",
         "backup_name": "", "backup_email": "",
         "plant_head_name": "", "plant_head_email": ""},
        {"id": "MADHAVARAM", "name": "Madhavaram Plant",
         "incharge_name": "", "incharge_email": "",
         "backup_name": "", "backup_email": "",
         "plant_head_name": "", "plant_head_email": ""},
        {"id": "THERVOY", "name": "Thervoykandigai Plant",
         "incharge_name": "", "incharge_email": "",
         "backup_name": "", "backup_email": "",
         "plant_head_name": "", "plant_head_email": ""},
        {"id": "HOSUR", "name": "Hosur Plant",
         "incharge_name": "", "incharge_email": "",
         "backup_name": "", "backup_email": "",
         "plant_head_name": "", "plant_head_email": ""},
    ],
    "priorities": ["Low", "Normal", "High", "Urgent"],
    "delivery_modes": ["Courier", "Vehicle", "In person"],
    "freight_terms": ["Paid", "To pay"],
    "responsible_depts": ["QC", "QA", "Production", "Warehouse", "Logistics", "R&D", "Stores"],
    "hold_reasons": [
        "Stock not available", "Batch under QC hold", "Awaiting QA release",
        "Pending production", "Packing material short", "Awaiting requester clarification",
        "Logistics unavailable",
    ],
    "reject_reasons": [
        "Item obsolete", "Request withdrawn", "Duplicate request",
        "Not manufactured at this plant", "Insufficient information",
    ],
    "discrepancy_types": ["Short quantity", "Damaged", "Wrong batch", "Wrong item", "Other"],
    "uoms": ["KG", "L", "G", "ML", "EA", "Nos", "Box"],
    # QA / QC and R&D-head oversight recipients (optional, used in CC where relevant)
    "qa_emails": [],
    # plants (by id) where a batch must be QA-released before a line can be dispatched
    "regulated_plants": [],
    # the SRDMS roles a user can be assigned (drives the role-based flow)
    "srdms_roles": ["R&D Requester", "Warehouse In-charge", "Warehouse Executive",
                    "QA / QC", "R&D Head / Plant Head", "System Administrator"],
    # per-user SRDMS role assignment: {user_code: {"role":..., "plant_id":...}} — a
    # warehouse user with a plant_id only acts on that plant's requests.
    "user_roles": {},
    # per-code email subject/body overrides ({} = use the built-in DEFAULT_TEMPLATES).
    # Admin-editable; placeholders like {sr_no}, {item}, {reason} are substituted at send.
    "email_templates": {},
    # base URL used for the {link} placeholder in emails (e.g. the app URL)
    "app_base_url": "",
    # SLA / configuration
    "sla": {
        "approval_required": False,      # route to R&D approver before the warehouse
        "approver_name": "", "approver_email": "",
        "ack_sla_hours": 24,             # unacknowledged beyond this -> N9 reminder + escalation
        "dispatch_sla_hours": 72,        # target dispatch TAT (for reporting / breach flag)
        "digest_hour": 9,                # daily pending digest hour (local, 24h)
        "sr_prefix": "SR/RD",            # request-number prefix -> SR/RD/2026/00147
    },
}

_EMPTY = {"requests": [], "notifications": [], "masters": {}, "seq": {}}


def _blank() -> dict:
    import copy
    return {"requests": [], "notifications": [], "masters": copy.deepcopy(DEFAULT_MASTERS),
            "seq": {}, "esc_flags": {}}


def load() -> dict:
    """Full store document, with master defaults overlaid for any missing keys."""
    with _LOCK:
        store = _blank()
        try:
            with open(_PATH, encoding="utf-8") as f:
                disk = json.load(f)
            for k in ("requests", "notifications", "seq", "esc_flags"):
                if k in disk:
                    store[k] = disk[k]
            # merge masters so newly-added default keys appear without wiping edits
            m = dict(DEFAULT_MASTERS)
            m.update(disk.get("masters", {}))
            if "sla" in disk.get("masters", {}):
                sla = dict(DEFAULT_MASTERS["sla"]); sla.update(disk["masters"]["sla"]); m["sla"] = sla
            store["masters"] = m
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return store


def save(store: dict) -> None:
    with _LOCK:
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2, default=str)
        os.replace(tmp, _PATH)


def mutate(fn):
    """Run fn(store) under the lock, persist, and return fn's result. fn mutates store
    in place and returns whatever the caller needs (e.g. the affected request)."""
    with _LOCK:
        store = load()
        result = fn(store)
        save(store)
        return result


def masters() -> dict:
    return load()["masters"]


def save_masters(updates: dict) -> dict:
    def _fn(store):
        m = store["masters"]
        for k, v in (updates or {}).items():
            if k == "sla" and isinstance(v, dict):
                sla = dict(m.get("sla", {})); sla.update(v); m["sla"] = sla
            elif k in DEFAULT_MASTERS:
                m[k] = v
        return m
    return mutate(_fn)


def bump_seq(store: dict, year: int) -> int:
    """Next running request number for a year, operating on the CALLER's store dict (so it
    persists via the caller's mutate — no nested mutate, which would be clobbered). Also
    scans already-issued SR numbers so a reset counter never re-issues a used number."""
    key = str(year)
    cur = int(store.get("seq", {}).get(key, 0))
    pat = re.compile(rf"/{key}/(\d+)\s*$")
    for r in store.get("requests", []):
        m = pat.search(r.get("sr_no") or "")
        if m:
            cur = max(cur, int(m.group(1)))
    n = cur + 1
    store.setdefault("seq", {})[key] = n
    return n


def next_seq(year: int) -> int:
    """Standalone next running number (own mutate). Prefer bump_seq(store, year) when
    already inside a mutate()."""
    return mutate(lambda store: bump_seq(store, year))
