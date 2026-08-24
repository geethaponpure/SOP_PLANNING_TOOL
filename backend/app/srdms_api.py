"""SRDMS REST API — Sample Request & Dispatch Management.

A self-contained APIRouter (mounted in main.py). All write actions accept an `actor`
{name,email,role} in the body — the app has no login layer, so the acting persona is
supplied by the client and recorded in the request's event log.
"""
from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import publish as _pub
from .integration import crm_sources as _crm
from .integration import srdms, srdms_store
from .integration import user_master as _um

router = APIRouter(prefix="/api/srdms", tags=["srdms"])

_UPLOAD_DIR = os.getenv("SRDMS_UPLOADS") or str(Path(__file__).resolve().parents[1] / "srdms_uploads")
_deliver_lock = threading.Lock()


def _deliver_async():
    """Autonomous delivery: send any unsent notifications in the background so every action's
    email goes out immediately without blocking the response or needing a manual click."""
    threading.Thread(target=_deliver_unsent, daemon=True).start()


class Actor(BaseModel):
    name: str = ""
    email: str = ""
    role: str = ""
    user_code: str = ""


class Line(BaseModel):
    item_code: str = ""
    item_desc: str = ""
    uom: str = "KG"
    qty_requested: float = 0
    remarks: str = ""


class RequestIn(BaseModel):
    requester_name: str = ""
    requester_email: str = ""
    requester_code: str = ""
    department: str = ""
    request_location: str = ""
    plant_id: str = ""
    priority: str = "Normal"
    required_by: str = ""
    rd_head: str = ""
    rd_head_code: str = ""
    purpose: str = ""
    lines: list[Line] = []
    actor: Actor = Actor()


class SubmitIn(BaseModel):
    actor: Actor = Actor()


class ApproveIn(BaseModel):
    decision: str = "approve"       # approve | reject
    reason: str = ""
    actor: Actor = Actor()


class AckIn(BaseModel):
    assign_to: str = ""
    actor: Actor = Actor()


class Batch(BaseModel):
    batch_no: str = ""
    qty: float = 0
    mfg_date: str = ""
    exp_date: str = ""


class DispatchIn(BaseModel):
    batches: list[Batch] = []
    batch_no: str = ""              # single-batch fallback
    qty: float = 0
    mfg_date: str = ""
    exp_date: str = ""
    mode: str = "Courier"
    mode_details: dict = {}
    packages: int | None = None
    freight: str = ""
    dispatch_date: str = ""
    expected_arrival: str = ""
    actor: Actor = Actor()


class HoldIn(BaseModel):
    reason: str = ""
    remarks: str = ""
    planned_date: str = ""
    responsible_dept: str = ""
    actor: Actor = Actor()


class RejectIn(BaseModel):
    reason: str = ""
    remarks: str = ""
    short_close: bool = False
    actor: Actor = Actor()


class ReceiptIn(BaseModel):
    condition: str = "Received in order"   # Received in order | Short quantity | Damaged | Wrong batch
    received_date: str = ""
    received_time: str = ""
    received_qty: float = 0
    received_by: str = ""
    remarks: str = ""
    actor: Actor = Actor()


class CancelIn(BaseModel):
    reason: str = ""
    actor: Actor = Actor()


class QaIn(BaseModel):
    decision: str = "Released"      # Released | Rejected
    remarks: str = ""
    actor: Actor = Actor()


def _a(actor: Actor) -> dict:
    return {"name": actor.name, "email": actor.email, "role": actor.role, "user_code": actor.user_code}


def _guard(fn):
    try:
        result = srdms.enrich(fn())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _deliver_async()          # auto-send any notifications this action produced
    return result


# ── masters ───────────────────────────────────────────────────────────────────
@router.get("/masters")
def get_masters():
    return srdms_store.masters()


@router.post("/masters")
def update_masters(updates: dict):
    return srdms_store.save_masters(updates)


class RoleIn(BaseModel):
    user_code: str
    role: str = ""
    plant_id: str = ""


@router.get("/items")
def items(q: str | None = None):
    """CRM item search for the Sample Request form (name first, code optional)."""
    if os.getenv("DATA_SOURCE", "synthetic").lower() != "live":
        return {"items": [], "note": "Requires DATA_SOURCE=live to search CRM items."}
    try:
        rows = _crm.crm_items(q)
    except Exception as e:   # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"CRM item read failed: {str(e).splitlines()[0][:150]}")
    return {"items": [{"name": r.get("Name"), "code": r.get("Code"), "uom": r.get("Uom")} for r in rows]}


@router.get("/user-roles")
def get_user_roles():
    m = srdms_store.masters()
    from .integration import app_roles as _roles
    # roles come from the Role Master (DB); fall back to the SRDMS default list.
    roles = _roles.role_names() or m.get("srdms_roles", [])
    return {"user_roles": m.get("user_roles", {}), "roles": roles}


@router.get("/rd-heads")
def rd_heads():
    """Users assigned the 'R&D Head / Plant Head' SRDMS role — for the requester's head picker."""
    ur = srdms_store.masters().get("user_roles", {})
    head_codes = {code for code, v in ur.items() if "Head" in (v or {}).get("role", "")}
    names = {u["user_code"]: u.get("name", "") for u in _um.list_users()}
    return {"heads": sorted(({"user_code": c, "name": names.get(c, c)} for c in head_codes),
                            key=lambda h: h["name"].lower())}


@router.post("/user-roles")
def set_user_role(body: RoleIn, actor_code: str = "", actor_name: str = ""):
    ur = srdms.set_user_role(body.user_code, body.role, body.plant_id)
    detail = (f"SRDMS role → {body.role}" + (f" @ {body.plant_id}" if body.plant_id else "")) if body.role else "SRDMS role cleared"
    _um.log_access(body.user_code, "", "role", detail, {"code": actor_code, "name": actor_name})
    return {"user_roles": ur}


# ── requests ──────────────────────────────────────────────────────────────────
@router.get("/requests")
def list_requests(status: str | None = None, plant_id: str | None = None,
                  requester: str | None = None, open_only: bool = False, q: str | None = None):
    st = srdms_store.load()
    return {"requests": srdms.list_requests(st, {"status": status, "plant_id": plant_id,
                                                 "requester": requester, "open_only": open_only, "q": q})}


@router.get("/requests/{sr_id}")
def get_request(sr_id: str):
    st = srdms_store.load()
    req = srdms._find(st, sr_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    return srdms.enrich(req)


@router.post("/requests")
def create_request(body: RequestIn):
    return _guard(lambda: srdms.create_request(body.model_dump(exclude={"actor"}), _a(body.actor)))


@router.put("/requests/{sr_id}")
def update_request(sr_id: str, body: RequestIn):
    return _guard(lambda: srdms.update_draft(sr_id, body.model_dump(exclude={"actor"}), _a(body.actor)))


@router.post("/requests/{sr_id}/submit")
def submit(sr_id: str, body: SubmitIn):
    return _guard(lambda: srdms.submit_request(sr_id, _a(body.actor)))


@router.post("/requests/{sr_id}/approve")
def approve(sr_id: str, body: ApproveIn):
    return _guard(lambda: srdms.approve_request(sr_id, body.decision, body.reason, _a(body.actor)))


@router.post("/requests/{sr_id}/acknowledge")
def acknowledge(sr_id: str, body: AckIn):
    return _guard(lambda: srdms.acknowledge(sr_id, body.assign_to, _a(body.actor)))


@router.post("/requests/{sr_id}/lines/{line_id}/dispatch")
def dispatch(sr_id: str, line_id: str, body: DispatchIn):
    return _guard(lambda: srdms.dispatch_line(sr_id, line_id, body.model_dump(exclude={"actor"}), _a(body.actor)))


@router.post("/requests/{sr_id}/lines/{line_id}/hold")
def hold(sr_id: str, line_id: str, body: HoldIn):
    return _guard(lambda: srdms.hold_line(sr_id, line_id, body.reason, body.remarks, body.planned_date,
                                          _a(body.actor), body.responsible_dept))


@router.post("/requests/{sr_id}/lines/{line_id}/reject")
def reject(sr_id: str, line_id: str, body: RejectIn):
    return _guard(lambda: srdms.reject_line(sr_id, line_id, body.reason, body.remarks, body.short_close, _a(body.actor)))


@router.post("/requests/{sr_id}/lines/{line_id}/receipt")
def receipt(sr_id: str, line_id: str, body: ReceiptIn):
    return _guard(lambda: srdms.receipt_line(sr_id, line_id, body.model_dump(exclude={"actor"}), _a(body.actor)))


@router.post("/requests/{sr_id}/lines/{line_id}/qa-release")
def qa_release(sr_id: str, line_id: str, body: QaIn):
    return _guard(lambda: srdms.qa_release(sr_id, line_id, body.decision, body.remarks, _a(body.actor)))


@router.post("/requests/{sr_id}/cancel")
def cancel(sr_id: str, body: CancelIn):
    return _guard(lambda: srdms.cancel_request(sr_id, body.reason, _a(body.actor)))


# ── attachments ───────────────────────────────────────────────────────────────
@router.post("/requests/{sr_id}/attachments")
async def upload_attachment(sr_id: str, file: UploadFile = File(...), line_id: str = Form(""),
                            kind: str = Form("attachment"), actor_name: str = Form(""),
                            actor_role: str = Form("")):
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    ext = Path(file.filename or "").suffix[:12]
    stored = f"{uuid.uuid4().hex}{ext}"
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File exceeds 25 MB limit")
    with open(os.path.join(_UPLOAD_DIR, stored), "wb") as f:
        f.write(data)
    try:
        req = srdms.add_attachment(sr_id, {"filename": file.filename, "stored": stored,
                                           "size": len(data), "kind": kind, "line_id": line_id},
                                   {"name": actor_name, "role": actor_role})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return srdms.enrich(req)


@router.get("/attachments/{stored}")
def download_attachment(stored: str):
    if "/" in stored or "\\" in stored or ".." in stored:
        raise HTTPException(status_code=400, detail="Invalid name")
    path = os.path.join(_UPLOAD_DIR, stored)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    # recover the original filename for the download prompt
    orig = stored
    for req in srdms_store.load()["requests"]:
        for a in req.get("attachments", []):
            if a.get("stored") == stored:
                orig = a.get("filename") or stored
    return FileResponse(path, filename=orig)


# ── dashboard / reports / notifications ───────────────────────────────────────
@router.get("/dashboard")
def dashboard():
    srdms_store.mutate(lambda st: srdms.evaluate_escalations(st))   # fire due N8/N9
    _deliver_async()          # auto-send any escalation emails just produced
    return srdms.dashboard(srdms_store.load())


def _tat_rows(st):
    rows = []
    for req in st["requests"]:
        if req["status"] in ("Draft", "Cancelled"):
            continue
        t = srdms._tat(req)
        rows.append({"sr_no": req.get("sr_no"), "requester": req.get("requester_name"),
                     "plant": req.get("plant_name"), "priority": req.get("priority"),
                     "status": req["status"], "submitted_at": req.get("submitted_at"),
                     "acknowledged_at": req.get("acknowledged_at"), "closed_at": req.get("closed_at"),
                     "ack_tat_h": t["ack_tat_h"], "total_tat_h": t["total_tat_h"],
                     "open_age_days": t["open_age_days"], "lines": len(req["lines"])})
    return rows


@router.get("/reports/tat")
def tat_report():
    return {"rows": _tat_rows(srdms_store.load())}


@router.get("/reports/export")
def export_reports():
    st = srdms_store.load()
    srdms.evaluate_escalations  # noqa (report reflects current store)
    data = _pub.build_srdms_reports_workbook(_tat_rows(st), srdms.dashboard(st))
    return StreamingResponse(
        iter([data]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="SRDMS_Reports.xlsx"'})


# ── email templates ───────────────────────────────────────────────────────────
@router.get("/email-templates")
def email_templates():
    """Effective templates per code: the admin override if set, else the built-in default,
    plus the list of placeholders available for each event."""
    overrides = srdms_store.masters().get("email_templates", {})
    out = {}
    for code, dflt in srdms.DEFAULT_TEMPLATES.items():
        ov = overrides.get(code) or {}
        out[code] = {"event": srdms._EVENT_NAME.get(code, code),
                     "subject": ov.get("subject", dflt["subject"]),
                     "body": ov.get("body", dflt["body"]),
                     "default_subject": dflt["subject"], "default_body": dflt["body"],
                     "overridden": bool(ov)}
    return {"templates": out}


@router.get("/notifications")
def notifications(unsent: bool = False, sr_id: str | None = None, limit: int = 200):
    st = srdms_store.load()
    ns = st["notifications"]
    if unsent:
        ns = [n for n in ns if not n.get("sent")]
    if sr_id:
        ns = [n for n in ns if n.get("sr_id") == sr_id or n.get("sr_no") == sr_id]
    return {"notifications": list(reversed(ns))[:limit]}


@router.post("/notifications/send-all")
def send_all():
    """Retry endpoint (emails now send automatically on every action). Delivers any
    still-unsent notifications synchronously — kept for manual retry / debugging."""
    sent, errors = _deliver_unsent()
    return {"sent": sent, "errors": errors, "smtp": bool(os.getenv("SRDMS_SMTP_HOST"))}


@router.post("/digest")
def run_digest():
    n = srdms_store.mutate(lambda st: srdms.build_daily_digest(st))
    _deliver_async()          # auto-send the digest emails
    return {"digests_created": n}


def _open_smtp():
    """Connect+login to the SMTP server (STARTTLS on 587). Returns (smtp, error)."""
    host = os.getenv("SRDMS_SMTP_HOST")
    if not host:
        return None, None
    import smtplib
    try:
        port = int(os.getenv("SRDMS_SMTP_PORT", "25"))
        smtp = smtplib.SMTP(host, port, timeout=20)
        smtp.ehlo()
        if os.getenv("SRDMS_SMTP_STARTTLS", "1") != "0" and smtp.has_extn("starttls"):
            smtp.starttls()
            smtp.ehlo()
        if os.getenv("SRDMS_SMTP_USER"):
            smtp.login(os.getenv("SRDMS_SMTP_USER"), os.getenv("SRDMS_SMTP_PASSWORD", ""))
        return smtp, None
    except Exception as e:   # noqa: BLE001
        return None, f"SMTP connect/login failed: {str(e).splitlines()[0][:180]}"


def _email_palette(n):
    """(accent, tint) colour by event sentiment — green good, red bad, amber caution, blue info."""
    s = (n.get("subject") or "").lower()
    if any(k in s for k in ("reject", "discrepancy", "overdue", "short-closed", "breach")):
        return "#dc2626", "#fef2f2"
    if any(k in s for k in ("approved", "received", "dispatched")):
        return "#16a34a", "#f0fdf4"
    if any(k in s for k in ("hold", "partial", "reminder", "revised", "pending")):
        return "#d97706", "#fffbeb"
    return "#2563eb", "#eff6ff"


def _email_html(n):
    """Decorative, email-client-safe HTML rendering of a notification."""
    import html as _h
    accent, tint = _email_palette(n)
    event = _h.escape(n.get("event") or n.get("code") or "Notification")
    sr = _h.escape(n.get("sr_no") or "")
    when = _h.escape(n.get("created_at") or "")
    rows = []
    for raw in (n.get("body") or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if ": " in line:
            lbl, val = line.split(": ", 1)
            if len(lbl) <= 22 and lbl.count(" ") <= 2 and not lbl.endswith("."):
                rows.append(
                    f'<tr><td style="padding:7px 12px;color:#64748b;font-size:13px;white-space:nowrap;'
                    f'vertical-align:top;font-weight:600">{_h.escape(lbl)}</td>'
                    f'<td style="padding:7px 12px;color:#0f172a;font-size:13px">{_h.escape(val)}</td></tr>')
                continue
        rows.append(f'<tr><td colspan="2" style="padding:7px 12px;color:#0f172a;font-size:13px;'
                    f'line-height:1.5">{_h.escape(line)}</td></tr>')
    body_rows = "".join(rows) or '<tr><td style="padding:10px 12px;color:#64748b">—</td></tr>'
    return f"""\
<div style="margin:0;padding:0;background:#f1f5f9;">
 <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 0;">
  <tr><td align="center">
   <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:600px;max-width:92%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(15,23,42,.10);font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
     <tr><td style="background:{accent};padding:20px 28px;">
       <table role="presentation" width="100%"><tr>
         <td style="color:#ffffff;font-size:18px;font-weight:700;letter-spacing:.4px;">&#129514; SRDMS</td>
         <td align="right"><span style="background:rgba(255,255,255,.22);color:#ffffff;font-size:11px;font-weight:600;padding:5px 12px;border-radius:999px;">{event}</span></td>
       </tr></table>
       <div style="color:rgba(255,255,255,.85);font-size:12px;margin-top:3px;">Sample Request &amp; Dispatch Management</div>
     </td></tr>
     <tr><td style="padding:22px 28px 4px;">
       <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.7px;">Request</div>
       <div style="font-size:22px;font-weight:700;color:#0f172a;">{sr}</div>
     </td></tr>
     <tr><td style="padding:10px 18px 20px;">
       <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{tint};border-radius:10px;border-collapse:separate;">{body_rows}</table>
     </td></tr>
     <tr><td style="padding:16px 28px 24px;border-top:1px solid #eef2f6;">
       <div style="font-size:12px;color:#94a3b8;">Automated notification &middot; {when}</div>
       <div style="font-size:11px;color:#cbd5e1;margin-top:4px;">Pure Chemicals &middot; Please do not reply to this email.</div>
     </td></tr>
   </table>
  </td></tr>
 </table>
</div>"""


def _build_msg(n):
    """Build a multipart/alternative message (plain-text + decorative HTML) for a
    notification dict (keys: subject, body, to, cc, code, event, sr_no, created_at)."""
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.utils import formataddr
    from_addr = os.getenv("SRDMS_SMTP_FROM") or os.getenv("SRDMS_SMTP_USER") or "srdms@pure-chemical.com"
    from_name = os.getenv("SRDMS_SMTP_FROM_NAME", "")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = n.get("subject") or "(no subject)"
    msg["From"] = formataddr((from_name, from_addr)) if from_name else from_addr
    msg["To"] = ", ".join(n.get("to") or [])
    if n.get("cc"):
        msg["Cc"] = ", ".join(n["cc"])
    msg.attach(MIMEText(n.get("body") or "", "plain", "utf-8"))
    msg.attach(MIMEText(_email_html(n), "html", "utf-8"))
    return from_addr, msg


def _deliver_unsent():
    """Send every unsent notification. Serialized (one delivery at a time) so a notification
    is never sent twice. If SMTP is configured but unreachable, nothing is marked sent (so the
    failure is visible). Without SMTP, the outbox is just marked sent."""
    with _deliver_lock:
        return _deliver_unsent_locked()


def _deliver_unsent_locked():
    st0 = srdms_store.load()
    if not any(not n.get("sent") for n in st0["notifications"]):
        return 0, []                          # nothing to send — don't even open SMTP
    host = os.getenv("SRDMS_SMTP_HOST")
    smtp, err = _open_smtp()
    if host and smtp is None:                 # configured but couldn't connect/login
        return 0, [err]
    sent, errors = 0, []

    def _fn(st):
        nonlocal sent, errors
        for n in st["notifications"]:
            if n.get("sent"):
                continue
            if smtp:
                recips = [r for r in (n.get("to", []) + n.get("cc", [])) if r and "@" in r]
                if not recips:
                    errors.append(f"{n['code']} {n.get('sr_no')}: no valid recipient — set emails in Masters")
                    continue
                try:
                    from_addr, msg = _build_msg(n)
                    smtp.sendmail(from_addr, recips, msg.as_string())
                except Exception as e:   # noqa: BLE001
                    errors.append(f"{n['code']} {n.get('sr_no')}: {str(e).splitlines()[0][:160]}")
                    continue
            n["sent"] = True
            n["sent_at"] = srdms._now_iso()
            sent += 1
    srdms_store.mutate(_fn)
    if smtp:
        try:
            smtp.quit()
        except Exception:   # noqa: BLE001
            pass
    return sent, errors
