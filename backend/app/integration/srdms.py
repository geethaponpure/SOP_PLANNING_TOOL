"""SRDMS workflow engine — state machine, notification outbox (matrix N1-N13),
TAT / ageing reports, dashboard and time-based escalations.

Lifecycle:  Draft -> Submitted -> [PendingApproval -> Approved/ApprovalRejected] ->
Acknowledged -> InProgress (per-line Dispatch / Hold / Reject / ShortClose) ->
receipt acknowledgement -> Closed (auto).  Cancel is allowed before acknowledgement.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from . import srdms_store as store

# ── status vocabularies ───────────────────────────────────────────────────────
# "Dispatched" is open until the requester confirms receipt (then it Closes).
REQ_OPEN = {"Submitted", "PendingApproval", "Approved", "Acknowledged", "InProgress", "Dispatched"}
LINE_TERMINAL = {"Dispatched", "Received", "ReceivedWithDiscrepancy", "Rejected", "ShortClosed"}
LINE_FINAL = {"Received", "ReceivedWithDiscrepancy", "Rejected", "ShortClosed"}  # closes the request


def _now() -> datetime:
    return datetime.now()


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat(sep=" ")


def _now_iso() -> str:
    return _iso(_now())


def _parse(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _hours(a, b) -> float | None:
    da, db = _parse(a), _parse(b)
    if not da or not db:
        return None
    return round((db - da).total_seconds() / 3600.0, 1)


# ── lookups ───────────────────────────────────────────────────────────────────
def _plant(masters, plant_id):
    for p in masters.get("plants", []):
        if p["id"] == plant_id:
            return p
    return None


def _find(st, sr_id):
    for r in st["requests"]:
        if r["id"] == sr_id or r.get("sr_no") == sr_id:
            return r
    return None


def _event(req, actor, action, detail=""):
    # every transaction records the acting user's id (user_code) + name + role
    req.setdefault("events", []).append(
        {"ts": _now_iso(), "actor": (actor or {}).get("name", "system"),
         "user_code": (actor or {}).get("user_code", ""), "role": (actor or {}).get("role", ""),
         "action": action, "detail": detail})


def _by(actor):
    """Standard actor stamp for action records: name + user id."""
    return {"by": (actor or {}).get("name", ""), "by_code": (actor or {}).get("user_code", "")}


# ── notification outbox (matrix N1-N13) ───────────────────────────────────────
def _clean(emails):
    seen, out = set(), []
    for e in emails:
        e = (e or "").strip()
        if e and e.lower() not in seen:
            seen.add(e.lower())
            out.append(e)
    return out


_EVENT_NAME = {
    "N1": "New request raised", "N1A": "Approval requested", "N2": "Request approved / rejected",
    "N3": "Request acknowledged", "N4": "Sample dispatched", "N5": "Partial dispatch",
    "N6": "Request put on hold", "N7": "Planned date revised", "N8": "Hold overdue / SLA breach",
    "N9": "Not acknowledged reminder", "N10": "Receipt confirmed", "N11": "Discrepancy reported",
    "N12": "Request cancelled", "N13": "Daily pending digest", "QA": "QA release / rejection",
}

# Built-in email templates. Admin overrides live in masters['email_templates'][code];
# an override's subject/body replaces the default. Placeholders are filled from the
# per-event context via str.format_map (a missing placeholder renders blank, never errors).
DEFAULT_TEMPLATES = {
    "N1": {"subject": "[New Sample Request] {sr_no} — {plant_name}",
           "body": "{hdr}\nRequest location: {request_location}\nItems: {items}\n"
                   "Please acknowledge and action. {link}"},
    "N1A": {"subject": "[Approval needed] Sample Request {sr_no}",
            "body": "{hdr}\nItems: {items}\nApprove to release to the warehouse. {link}"},
    "N2": {"subject": "[{decision}] {sr_no}{item_suffix}",
           "body": "{decision}. Reason: {reason} {remarks}"},
    "N3": {"subject": "[Acknowledged] Sample Request {sr_no}",
           "body": "Acknowledged by {ack_by} at {ack_at}. {assign}"},
    "N4": {"subject": "[Dispatched] {sr_no} — {item}",
           "body": "{item}: batch {batch}, qty {qty} {uom}.\nMode: {mode_line}\n"
                   "Expected arrival: {eta} {link}"},
    "N5": {"subject": "[Partial dispatch] {sr_no} — {item}",
           "body": "{item}: delivered {delivered} of {requested} {uom} (balance {balance}). "
                   "Batch {batch} via {mode_line}."},
    "N6": {"subject": "[On hold] {sr_no} — {item}",
           "body": "{item} placed on hold. Reason: {reason}. Remarks: {remarks}. "
                   "Planned delivery: {planned_date}."},
    "N7": {"subject": "[Hold date revised] {sr_no} — {item}",
           "body": "Planned delivery date changed {old_date} → {planned_date}. Reason: {reason}"},
    "N8": {"subject": "[Hold overdue] {sr_no} — {item}",
           "body": "Hold planned date {planned_date} has passed. Ageing {ageing_days} days. "
                   "Reason: {reason}."},
    "N9": {"subject": "[Reminder] Unacknowledged Sample Request {sr_no}",
           "body": "{hdr} is unacknowledged for {hours} h (SLA {sla_hours} h)."},
    "N10": {"subject": "[Received] {sr_no} — {item}",
            "body": "{item} received. Total TAT: {total_tat} h"},
    "N11": {"subject": "[Discrepancy] {sr_no} — {item}",
            "body": "Discrepancy: {discrepancy_type}. Remarks: {remarks} {attachments}"},
    "N12": {"subject": "[Cancelled] {sr_no}", "body": "Request cancelled. Reason: {reason}"},
    "N13": {"subject": "[Daily pending digest] {plant_name} — {open_count} open",
            "body": "{digest_body}"},
    "QA": {"subject": "[QA {qa_decision}] {sr_no} — {item}",
           "body": "QA {qa_decision} the batch for {item}. {remarks}"},
}


class _Safe(dict):
    def __missing__(self, k):
        return ""


def _render(tpl, ctx):
    try:
        return str(tpl).format_map(_Safe(ctx))
    except (ValueError, KeyError, IndexError):
        return str(tpl)


def _link(st, req):
    base = (st.get("masters", {}).get("app_base_url") or "").strip()
    return f"{base}?sr={req.get('sr_no') or req.get('id')}" if base else ""


def _base_ctx(st, req):
    return {"sr_no": req.get("sr_no") or req.get("id"), "requester_name": req.get("requester_name", ""),
            "requester_email": req.get("requester_email", ""), "plant_name": req.get("plant_name", ""),
            "priority": req.get("priority", ""), "required_by": req.get("required_by") or "—",
            "request_location": req.get("request_location") or "—", "items": _item_table(req),
            "purpose": req.get("purpose", ""), "status": req.get("status", ""),
            "hdr": _hdr(req), "link": _link(st, req), "item": "", "item_suffix": ""}


def _emit(st, code, req, to, cc, ctx=None):
    """Render the code's template (admin override else default) and queue the notification."""
    c = _base_ctx(st, req)
    c.update(ctx or {})
    tpl = ((st.get("masters", {}).get("email_templates") or {}).get(code)
           or DEFAULT_TEMPLATES.get(code, {"subject": code, "body": ""}))
    st["notifications"].append({
        "id": _new_id(), "code": code, "sr_no": req.get("sr_no") or req.get("id"),
        "sr_id": req.get("id"), "event": _EVENT_NAME.get(code, code),
        "to": _clean(to or []), "cc": _clean(cc or []),
        "subject": _render(tpl.get("subject", code), c), "body": _render(tpl.get("body", ""), c),
        "created_at": _now_iso(), "sent": False})


def _wh(plant):
    return ([plant.get("incharge_email")] if plant else [], [plant.get("backup_email")] if plant else [])


def _item_table(req):
    return "; ".join(f"{l['item_desc']} × {l['qty_requested']} {l.get('uom', '')}".strip()
                     for l in req["lines"])


def _hdr(req):
    return (f"{req.get('sr_no') or req['id']} · {req.get('requester_name', '')} · "
            f"plant {req.get('plant_name', '')} · priority {req.get('priority', '')} · "
            f"required-by {req.get('required_by', '—')}")


# ── create / edit / submit ────────────────────────────────────────────────────
def _mk_line(l):
    return {"line_id": _new_id(), "item_code": (l.get("item_code") or "").strip(),
            "item_desc": (l.get("item_desc") or "").strip(),
            "uom": (l.get("uom") or "KG").strip(),
            "qty_requested": round(float(l.get("qty_requested") or 0), 3),
            "remarks": (l.get("remarks") or "").strip(),
            "status": "Open", "qty_dispatched": 0.0, "qa_status": None, "qa": None,
            "dispatches": [], "hold": None, "reject": None, "receipt": None}


def create_request(payload, actor) -> dict:
    def _fn(st):
        p = _plant(st["masters"], payload.get("plant_id"))
        req = {
            "id": _new_id(), "sr_no": None, "status": "Draft",
            "requester_name": (payload.get("requester_name") or (actor or {}).get("name") or "").strip(),
            "requester_code": (payload.get("requester_code") or (actor or {}).get("user_code") or "").strip(),
            "requester_email": (payload.get("requester_email") or (actor or {}).get("email") or "").strip(),
            "department": (payload.get("department") or "").strip(),
            "request_location": (payload.get("request_location") or "").strip(),
            "plant_id": payload.get("plant_id"), "plant_name": p["name"] if p else payload.get("plant_id"),
            "priority": payload.get("priority") or "Normal",
            "required_by": payload.get("required_by") or "",
            "rd_head": (payload.get("rd_head") or "").strip(),
            "rd_head_code": (payload.get("rd_head_code") or "").strip(),
            "purpose": (payload.get("purpose") or "").strip(),
            "created_at": _now_iso(), "submitted_at": None, "acknowledged_at": None,
            "acknowledged_by": None, "assigned_to": None,
            "approval_required": False, "approval_status": None, "approval_at": None,
            "approval_reason": None, "approver_email": None,
            "closed_at": None, "cancelled_at": None, "cancel_reason": None,
            "lines": [_mk_line(l) for l in (payload.get("lines") or []) if (l.get("item_desc") or l.get("item_code"))],
            "attachments": [], "events": [],
        }
        _event(req, actor, "Created", f"{len(req['lines'])} line(s)")
        st["requests"].insert(0, req)
        return req
    return store.mutate(_fn)


def update_draft(sr_id, payload, actor) -> dict:
    def _fn(st):
        req = _find(st, sr_id)
        if not req:
            raise ValueError("Request not found")
        if req["status"] != "Draft":
            raise ValueError("Only draft requests can be edited")
        p = _plant(st["masters"], payload.get("plant_id", req["plant_id"]))
        for k in ("requester_name", "requester_email", "department", "request_location",
                  "priority", "required_by", "purpose", "rd_head", "rd_head_code"):
            if k in payload:
                req[k] = payload[k]
        if "plant_id" in payload:
            req["plant_id"] = payload["plant_id"]
            req["plant_name"] = p["name"] if p else payload["plant_id"]
        if "lines" in payload:
            req["lines"] = [_mk_line(l) for l in payload["lines"] if (l.get("item_desc") or l.get("item_code"))]
        _event(req, actor, "Edited draft")
        return req
    return store.mutate(_fn)


def submit_request(sr_id, actor) -> dict:
    def _fn(st):
        req = _find(st, sr_id)
        if not req:
            raise ValueError("Request not found")
        if req["status"] != "Draft":
            raise ValueError("Only draft requests can be submitted")
        if not req["lines"]:
            raise ValueError("Add at least one item line before submitting")
        year = _now().year
        req["sr_no"] = f"{st['masters']['sla'].get('sr_prefix', 'SR/RD')}/{year}/{store.bump_seq(st, year):05d}"
        req["submitted_at"] = _now_iso()
        sla = st["masters"]["sla"]
        plant = _plant(st["masters"], req["plant_id"])
        if sla.get("approval_required") and sla.get("approver_email"):
            req["approval_required"] = True
            req["approval_status"] = "Pending"
            req["approver_email"] = sla.get("approver_email")
            req["status"] = "PendingApproval"
            _event(req, actor, "Submitted (awaiting approval)", req["sr_no"])
            _emit(st, "N1A", req, [sla.get("approver_email")], [req["requester_email"]])
        else:
            req["status"] = "Submitted"
            _event(req, actor, "Submitted", req["sr_no"])
            inc, bkp = _wh(plant)
            _emit(st, "N1", req, inc, bkp + [req["requester_email"]])
        return req
    return store.mutate(_fn)


def approve_request(sr_id, decision, reason, actor) -> dict:
    def _fn(st):
        req = _find(st, sr_id)
        if not req or req["status"] != "PendingApproval":
            raise ValueError("Request is not awaiting approval")
        plant = _plant(st["masters"], req["plant_id"])
        req["approval_at"] = _now_iso()
        req["approval_reason"] = reason or ""
        if decision == "approve":
            req["approval_status"] = "Approved"
            req["status"] = "Submitted"
            _event(req, actor, "Approved")
            inc, bkp = _wh(plant)
            _emit(st, "N2", req, [req["requester_email"]], inc,
                  {"decision": "Approved", "reason": reason or "—", "remarks": ""})
            _emit(st, "N1", req, inc, bkp + [req["requester_email"]])
        else:
            req["approval_status"] = "Rejected"
            req["status"] = "ApprovalRejected"
            _event(req, actor, "Approval rejected", reason or "")
            _emit(st, "N2", req, [req["requester_email"]], [],
                  {"decision": "Rejected", "reason": reason or "—", "remarks": ""})
        return req
    return store.mutate(_fn)


def acknowledge(sr_id, assign_to, actor) -> dict:
    def _fn(st):
        req = _find(st, sr_id)
        if not req or req["status"] != "Submitted":
            raise ValueError("Only a submitted (approved) request can be acknowledged")
        req["acknowledged_at"] = _now_iso()
        req["acknowledged_by"] = (actor or {}).get("name", "")
        req["acknowledged_by_code"] = (actor or {}).get("user_code", "")
        req["assigned_to"] = assign_to or ""
        req["status"] = "Acknowledged"
        _event(req, actor, "Acknowledged", f"assigned to {assign_to}" if assign_to else "")
        _emit(st, "N3", req, [req["requester_email"]], [],
              {"ack_by": req["acknowledged_by"], "ack_at": req["acknowledged_at"],
               "assign": f"Assigned to {assign_to}" if assign_to else ""})
        return req
    return store.mutate(_fn)


def _line(req, line_id):
    for l in req["lines"]:
        if l["line_id"] == line_id:
            return l
    raise ValueError("Line not found")


def _require_actionable(req):
    # a Submitted request can be actioned directly by the warehouse (no separate
    # acknowledge step) — PendingApproval still needs approval first.
    if req["status"] not in ("Submitted", "Acknowledged", "InProgress"):
        raise ValueError("Request must be submitted (and approved, if required) before actioning")


def _ensure_ack(req, actor):
    """Silently record the acknowledgement on the FIRST warehouse action of a Submitted
    request (captures Ack-TAT) so no explicit Acknowledge click is needed."""
    if not req.get("acknowledged_at"):
        req["acknowledged_at"] = _now_iso()
        req["acknowledged_by"] = (actor or {}).get("name", "")
        req["acknowledged_by_code"] = (actor or {}).get("user_code", "")
    if req["status"] == "Submitted":
        req["status"] = "Acknowledged"


def dispatch_line(sr_id, line_id, dispatch, actor) -> dict:
    """Record a dispatch (Form B). dispatch: {batches:[{batch_no,qty,mfg_date,exp_date}],
    mode, mode_details, packages, freight, dispatch_date, expected_arrival}. A single
    {batch_no,qty,...} is also accepted. Multiple calls accumulate onto the line (split)."""
    def _fn(st):
        req = _find(st, sr_id)
        if not req:
            raise ValueError("Request not found")
        _require_actionable(req)
        _ensure_ack(req, actor)
        l = _line(req, line_id)
        # QA release gate: at regulated plants a line's batch must be QA-released first.
        if req.get("plant_id") in st["masters"].get("regulated_plants", []) and l.get("qa_status") != "Released":
            raise ValueError("QA release required before dispatch (regulated plant)")
        raw = dispatch.get("batches")
        if not raw:
            raw = [{"batch_no": dispatch.get("batch_no", ""), "qty": dispatch.get("qty", 0),
                    "mfg_date": dispatch.get("mfg_date", ""), "exp_date": dispatch.get("exp_date", "")}]
        batches, total = [], 0.0
        for b in raw:
            q = round(float(b.get("qty") or 0), 3)
            if q <= 0:
                continue
            batches.append({"batch_no": (b.get("batch_no") or "").strip(), "qty": q,
                            "mfg_date": b.get("mfg_date") or "", "exp_date": b.get("exp_date") or ""})
            total += q
        if total <= 0:
            raise ValueError("Enter at least one batch with quantity > 0")
        total = round(total, 3)
        mode = dispatch.get("mode") or "Courier"
        batch_label = ", ".join(b["batch_no"] for b in batches if b["batch_no"]) or "—"
        d = {"batches": batches, "batch_no": batch_label, "qty": total, "mode": mode,
             "mode_details": dispatch.get("mode_details") or {},
             "packages": dispatch.get("packages"), "freight": dispatch.get("freight") or "",
             "dispatch_date": dispatch.get("dispatch_date") or _now().date().isoformat(),
             "expected_arrival": dispatch.get("expected_arrival") or "",
             "dispatched_at": _now_iso(), "dispatched_by": (actor or {}).get("name", ""),
             "dispatched_by_code": (actor or {}).get("user_code", "")}
        l["dispatches"].append(d)
        l["qty_dispatched"] = round(sum(x["qty"] for x in l["dispatches"]), 3)
        if l.get("hold"):            # a dispatch clears any hold on the line
            l["hold"] = None
        full = l["qty_dispatched"] + 1e-9 >= l["qty_requested"]
        l["status"] = "Dispatched" if full else "PartiallyDispatched"
        req["status"] = "InProgress"
        _event(req, actor, "Dispatched",
               f"{l['item_desc']} batch {batch_label} qty {total} via {mode}")
        plant = _plant(st["masters"], req["plant_id"])
        inc, _ = _wh(plant)
        md = _mode_line(d)
        pk = (f", {d['packages']} package(s)" if d.get("packages") else "") + \
             (f", freight {d['freight']}" if d.get("freight") else "")
        ctx = {"item": l["item_desc"], "batch": batch_label, "qty": total, "uom": l.get("uom", ""),
               "mode_line": md + pk, "eta": d["expected_arrival"] or "—"}
        if full:
            _emit(st, "N4", req, [req["requester_email"]], inc, ctx)
        else:
            bal = round(l["qty_requested"] - l["qty_dispatched"], 3)
            _emit(st, "N5", req, [req["requester_email"]], inc,
                  {**ctx, "delivered": l["qty_dispatched"], "requested": l["qty_requested"], "balance": bal})
        _recompute(req)
        return req
    return store.mutate(_fn)


def _mode_line(d):
    m, det = d.get("mode"), d.get("mode_details") or {}
    if m == "Courier":
        return f"Courier {det.get('courier_name', '')} AWB {det.get('awb_no', '')} {det.get('tracking_link', '')}".strip()
    if m == "Vehicle":
        return f"Vehicle {det.get('vehicle_no', '')} driver {det.get('driver_name', '')} ({det.get('driver_contact', '')})".strip()
    if m in ("Hand delivery", "In person"):
        return f"{m} via {det.get('person_name', '')} ({det.get('contact', '')})".strip()
    return m or ""


def hold_line(sr_id, line_id, reason, remarks, planned_date, actor, responsible_dept="") -> dict:
    def _fn(st):
        req = _find(st, sr_id)
        if not req:
            raise ValueError("Request not found")
        _require_actionable(req)
        _ensure_ack(req, actor)
        l = _line(req, line_id)
        if not reason:
            raise ValueError("Hold reason is required")
        prev = l.get("hold")
        revised = bool(prev)
        hist = (prev or {}).get("history", [])
        if prev:
            hist = hist + [{"planned_date": prev.get("planned_date"), "reason": prev.get("reason"),
                            "at": prev.get("set_at")}]
        l["hold"] = {"reason": reason, "remarks": remarks or "", "planned_date": planned_date or "",
                     "responsible_dept": responsible_dept or "", "set_at": _now_iso(),
                     "set_by": (actor or {}).get("name", ""), "set_by_code": (actor or {}).get("user_code", ""),
                     "history": hist}
        l["status"] = "Hold"
        req["status"] = "InProgress"
        _event(req, actor, "Hold revised" if revised else "Hold",
               f"{l['item_desc']}: {reason} (planned {planned_date})")
        ctx = {"item": l["item_desc"], "reason": reason, "remarks": remarks or "—",
               "planned_date": planned_date or "—", "responsible_dept": responsible_dept or "—"}
        if revised and prev:
            _emit(st, "N7", req, [req["requester_email"]], [],
                  {**ctx, "old_date": prev.get("planned_date") or "—"})
        else:
            _emit(st, "N6", req, [req["requester_email"]], [], ctx)
        _recompute(req)
        return req
    return store.mutate(_fn)


def reject_line(sr_id, line_id, reason, remarks, short_close, actor) -> dict:
    def _fn(st):
        req = _find(st, sr_id)
        if not req:
            raise ValueError("Request not found")
        _require_actionable(req)
        _ensure_ack(req, actor)
        l = _line(req, line_id)
        if not reason:
            raise ValueError("A reason is mandatory to reject / short-close")
        l["reject"] = {"reason": reason, "remarks": remarks or "", "short_close": bool(short_close),
                       "at": _now_iso(), **_by(actor)}
        l["status"] = "ShortClosed" if short_close else "Rejected"
        _event(req, actor, "Short-closed" if short_close else "Rejected", f"{l['item_desc']}: {reason}")
        plant = _plant(st["masters"], req["plant_id"])
        inc, _ = _wh(plant)
        _emit(st, "N2", req, [req["requester_email"]], inc,
              {"decision": "Short-closed" if short_close else "Rejected", "reason": reason,
               "remarks": remarks or "", "item": l["item_desc"], "item_suffix": f" — {l['item_desc']}"})
        _recompute(req)
        return req
    return store.mutate(_fn)


def receipt_line(sr_id, line_id, receipt, actor) -> dict:
    """Requester confirms receipt (Form C). receipt: {condition, received_date, received_time,
    received_qty, received_by, remarks}. condition 'Received in order' => Received, else a
    discrepancy (Short quantity / Damaged / Wrong batch)."""
    def _fn(st):
        req = _find(st, sr_id)
        if not req:
            raise ValueError("Request not found")
        l = _line(req, line_id)
        if l["status"] not in ("Dispatched", "PartiallyDispatched"):
            raise ValueError("Only a dispatched line can be marked received")
        condition = receipt.get("condition") or "Received in order"
        disc = condition != "Received in order"
        remarks = receipt.get("remarks") or ""
        if disc and not remarks.strip():
            raise ValueError("Discrepancy remarks are mandatory unless received in order")
        status = "ReceivedWithDiscrepancy" if disc else "Received"
        l["receipt"] = {"status": status, "condition": condition,
                        "discrepancy_type": condition if disc else "",
                        "received_date": receipt.get("received_date") or _now().date().isoformat(),
                        "received_time": receipt.get("received_time") or "",
                        "received_qty": round(float(receipt.get("received_qty") or 0), 3),
                        "received_by": receipt.get("received_by") or (actor or {}).get("name", ""),
                        "remarks": remarks, "at": _now_iso(), **_by(actor)}
        l["status"] = status
        discrepancy_type = condition if disc else ""
        _event(req, actor, "Received (discrepancy)" if disc else "Received",
               f"{l['item_desc']} {discrepancy_type or ''}")
        plant = _plant(st["masters"], req["plant_id"])
        inc, _ = _wh(plant)
        atts = [a["filename"] for a in req.get("attachments", []) if a.get("line_id") == line_id]
        if disc:
            ph = [plant.get("plant_head_email")] if plant else []
            _emit(st, "N11", req, inc, ph + st["masters"].get("qa_emails", []),
                  {"item": l["item_desc"], "discrepancy_type": discrepancy_type or "—",
                   "remarks": remarks or "—", "attachments": f"Attachments: {', '.join(atts)}" if atts else ""})
        else:
            _emit(st, "N10", req, inc, [req["requester_email"]],
                  {"item": l["item_desc"], "total_tat": _hours(req["submitted_at"], _now_iso())})
        _recompute(req)
        return req
    return store.mutate(_fn)


def qa_release(sr_id, line_id, decision, remarks, actor) -> dict:
    """QA / QC releases or rejects a line's batch (required before dispatch at regulated
    plants). decision = 'Released' | 'Rejected'."""
    def _fn(st):
        req = _find(st, sr_id)
        if not req:
            raise ValueError("Request not found")
        if req["status"] not in ("Acknowledged", "InProgress", "Submitted"):
            raise ValueError("QA release applies to an in-progress request")
        l = _line(req, line_id)
        rel = decision == "Released"
        l["qa_status"] = "Released" if rel else "Rejected"
        l["qa"] = {"decision": l["qa_status"], "remarks": remarks or "",
                   "at": _now_iso(), **_by(actor)}
        _event(req, actor, f"QA {l['qa_status']}", f"{l['item_desc']}: {remarks or ''}")
        plant = _plant(st["masters"], req["plant_id"])
        inc, _ = _wh(plant)
        _emit(st, "QA", req, inc, [req["requester_email"]],
              {"item": l["item_desc"], "qa_decision": l["qa_status"], "remarks": remarks or ""})
        return req
    return store.mutate(_fn)


def add_attachment(sr_id, meta, actor) -> dict:
    """Record an uploaded file against a request (optionally a specific line). meta:
    {filename, stored, size, kind, line_id}. The bytes are saved by the API layer."""
    def _fn(st):
        req = _find(st, sr_id)
        if not req:
            raise ValueError("Request not found")
        att = {"id": _new_id(), "filename": meta.get("filename", ""), "stored": meta.get("stored", ""),
               "size": int(meta.get("size") or 0), "kind": meta.get("kind") or "attachment",
               "line_id": meta.get("line_id") or "", "by": (actor or {}).get("name", ""),
               "at": _now_iso()}
        req.setdefault("attachments", []).append(att)
        _event(req, actor, "Attachment added", att["filename"])
        return req
    return store.mutate(_fn)


def cancel_request(sr_id, reason, actor) -> dict:
    def _fn(st):
        req = _find(st, sr_id)
        if not req:
            raise ValueError("Request not found")
        if req["status"] in ("Closed", "Cancelled"):
            raise ValueError("Request is already closed/cancelled")
        if any(l["status"] not in ("Open", "Hold") for l in req["lines"]):
            raise ValueError("Cannot cancel — some lines are already dispatched/received")
        req["status"] = "Cancelled"
        req["cancelled_at"] = _now_iso()
        req["cancel_reason"] = reason or ""
        _event(req, actor, "Cancelled", reason or "")
        plant = _plant(st["masters"], req["plant_id"])
        inc, _ = _wh(plant)
        _emit(st, "N12", req, inc, [], {"reason": reason or "—"})
        return req
    return store.mutate(_fn)


def _recompute(req):
    if req["status"] in ("Draft", "Submitted", "PendingApproval", "ApprovalRejected", "Cancelled"):
        return
    lines = req["lines"]
    if lines and all(l["status"] in LINE_FINAL for l in lines):
        # every line received / rejected / short-closed -> the request Closes
        if req["status"] != "Closed":
            req["status"] = "Closed"
            req["closed_at"] = req.get("closed_at") or _now_iso()
    elif lines and all(l["status"] in LINE_TERMINAL for l in lines):
        # all lines dispatched/settled but at least one awaits receipt -> Dispatched
        req["status"] = "Dispatched"
    elif any(l["status"] != "Open" for l in lines):
        req["status"] = "InProgress"
    else:
        req["status"] = "Acknowledged"


# ── derived views ─────────────────────────────────────────────────────────────
def _tat(req):
    now = _now_iso()
    ack = _hours(req.get("submitted_at"), req.get("acknowledged_at"))
    total = _hours(req.get("submitted_at"), req.get("closed_at")) if req.get("closed_at") else None
    open_age = None
    if req["status"] in REQ_OPEN:
        open_age = _hours(req.get("submitted_at"), now)
    return {"ack_tat_h": ack, "total_tat_h": total, "open_age_h": open_age,
            "open_age_days": round(open_age / 24, 1) if open_age is not None else None}


def enrich(req):
    r = dict(req)
    r["tat"] = _tat(req)
    r["line_count"] = len(req["lines"])
    r["qty_requested_total"] = round(sum(l["qty_requested"] for l in req["lines"]), 3)
    r["qty_dispatched_total"] = round(sum(l.get("qty_dispatched", 0) for l in req["lines"]), 3)
    r["held_lines"] = sum(1 for l in req["lines"] if l["status"] == "Hold")
    r["open_lines"] = sum(1 for l in req["lines"] if l["status"] in ("Open", "Hold", "PartiallyDispatched"))
    return r


def list_requests(st, filters=None):
    f = filters or {}
    out = []
    for req in st["requests"]:
        if f.get("status") and req["status"] != f["status"]:
            continue
        if f.get("plant_id") and req.get("plant_id") != f["plant_id"]:
            continue
        if f.get("requester") and f["requester"].lower() not in (req.get("requester_email", "") + req.get("requester_name", "")).lower():
            continue
        if f.get("open_only") and req["status"] not in REQ_OPEN:
            continue
        if f.get("q"):
            ql = f["q"].lower()
            hay = (req.get("sr_no", "") + req.get("requester_name", "") + " "
                   + " ".join(l["item_desc"] for l in req["lines"])).lower()
            if ql not in hay:
                continue
        out.append(enrich(req))
    return out


def set_user_role(user_code, role, plant_id=""):
    """Assign a user's SRDMS role (+ optional plant binding). Empty role clears it."""
    def _fn(st):
        ur = st["masters"].setdefault("user_roles", {})
        if role:
            ur[str(user_code)] = {"role": role, "plant_id": plant_id or ""}
        else:
            ur.pop(str(user_code), None)
        return ur
    return store.mutate(_fn)


def dashboard(st):
    reqs = st["requests"]
    by_status = {}
    for r in reqs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    open_reqs = [r for r in reqs if r["status"] in REQ_OPEN]
    held = [(r, l) for r in reqs for l in r["lines"] if l["status"] == "Hold"]
    now = _now()

    def _age_days(r):
        d = _parse(r.get("submitted_at"))
        return round((now - d).total_seconds() / 86400, 1) if d else 0

    ack_tats = [t for r in reqs if (t := _hours(r.get("submitted_at"), r.get("acknowledged_at"))) is not None]
    tot_tats = [t for r in reqs if r.get("closed_at") and (t := _hours(r.get("submitted_at"), r.get("closed_at"))) is not None]
    sla = st["masters"]["sla"]
    ack_breach = [r for r in reqs if r["status"] == "Submitted"
                  and (_hours(r.get("submitted_at"), _now_iso()) or 0) > sla.get("ack_sla_hours", 24)]
    hold_overdue = [(r, l) for (r, l) in held if l.get("hold", {}).get("planned_date")
                    and _parse(l["hold"]["planned_date"]) and _parse(l["hold"]["planned_date"]) < now]
    aging_buckets = {"0-1d": 0, "1-3d": 0, "3-7d": 0, ">7d": 0}
    for r in open_reqs:
        a = _age_days(r)
        aging_buckets["0-1d" if a <= 1 else "1-3d" if a <= 3 else "3-7d" if a <= 7 else ">7d"] += 1

    return {
        "totals": {
            "all": len(reqs), "open": len(open_reqs), "closed": by_status.get("Closed", 0),
            "held_lines": len(held), "ack_breach": len(ack_breach), "hold_overdue": len(hold_overdue),
            "cancelled": by_status.get("Cancelled", 0),
        },
        "by_status": by_status,
        "avg_ack_tat_h": round(sum(ack_tats) / len(ack_tats), 1) if ack_tats else None,
        "avg_total_tat_h": round(sum(tot_tats) / len(tot_tats), 1) if tot_tats else None,
        "aging_buckets": aging_buckets,
        "open_requests": sorted((enrich(r) for r in open_reqs),
                                key=lambda r: -(r["tat"]["open_age_h"] or 0))[:50],
        "hold_overdue_lines": [{"sr_no": r.get("sr_no"), "sr_id": r["id"], "item": l["item_desc"],
                                "planned_date": l["hold"]["planned_date"], "reason": l["hold"]["reason"],
                                "requester": r.get("requester_name")} for (r, l) in hold_overdue],
    }


def evaluate_escalations(st):
    """Lazily fire time-based notifications (N9 ack-reminder, N8 hold-overdue). Guarded by
    esc_flags so each condition emails at most once. Returns the number fired."""
    now = _now()
    sla = st["masters"]["sla"]
    flags = st.setdefault("esc_flags", {})
    fired = 0
    for req in st["requests"]:
        plant = _plant(st["masters"], req.get("plant_id"))
        inc, bkp = _wh(plant)
        ph = [plant.get("plant_head_email")] if plant else []
        # N9 — submitted but not acknowledged beyond SLA
        if req["status"] == "Submitted":
            h = _hours(req.get("submitted_at"), _now_iso())
            key = f"N9:{req['id']}"
            if h is not None and h > sla.get("ack_sla_hours", 24) and key not in flags:
                _emit(st, "N9", req, inc, bkp + ph,
                      {"hours": h, "sla_hours": sla.get("ack_sla_hours", 24)})
                flags[key] = _now_iso()
                fired += 1
        # N8 — a hold whose planned delivery date has passed
        for l in req["lines"]:
            hold = l.get("hold")
            if l["status"] == "Hold" and hold and hold.get("planned_date"):
                pd = _parse(hold["planned_date"])
                key = f"N8:{l['line_id']}:{hold['planned_date']}"
                if pd and pd < now and key not in flags:
                    age = round((now - _parse(req["submitted_at"])).total_seconds() / 86400, 1) if req.get("submitted_at") else None
                    _emit(st, "N8", req, inc, ph,
                          {"item": l["item_desc"], "planned_date": hold["planned_date"],
                           "ageing_days": age, "reason": hold["reason"]})
                    flags[key] = _now_iso()
                    fired += 1
    return fired


def build_daily_digest(st):
    """N13 — one pending digest per plant with open/held lines + ageing. Returns count."""
    now = _now()
    by_plant = {}
    for req in st["requests"]:
        if req["status"] not in REQ_OPEN:
            continue
        by_plant.setdefault(req.get("plant_id"), []).append(req)
    n = 0
    for plant_id, reqs in by_plant.items():
        plant = _plant(st["masters"], plant_id)
        inc, _ = _wh(plant)
        ph = [plant.get("plant_head_email")] if plant else []
        lines = []
        for r in reqs:
            age = round((now - _parse(r["submitted_at"])).total_seconds() / 86400, 1) if r.get("submitted_at") else 0
            lines.append(f"{r.get('sr_no')} · {r.get('requester_name')} · {r['status']} · {age}d · "
                         f"{sum(1 for l in r['lines'] if l['status'] in ('Open', 'Hold', 'PartiallyDispatched'))} open line(s)")
        _emit(st, "N13", {"id": f"digest-{plant_id}", "sr_no": f"DIGEST/{plant_id}",
                          "plant_name": plant["name"] if plant else plant_id},
              inc, ph, {"plant_name": plant["name"] if plant else plant_id,
                        "open_count": len(reqs), "digest_body": "\n".join(lines)})
        n += 1
    return n
