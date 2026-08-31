"""Demand-side + blueprint pages: cockpit, DQ, validation, forecasting,
segmentation, confirmations, consensus lock, analytics, KPIs, governance, audit."""
from ._deps import *

router = APIRouter()

REASON_CODES = [
    "Confirmed firm deal / tender awarded", "New customer or new market entry",
    "Promotion or campaign", "Price-driven pull-forward / pre-buy",
    "Lost business / churn", "Competitive pressure", "Regulatory or seasonal shift",
    "Correction of prior double-count", "Other (free text)",
]


@router.get("/api/overview")
def get_overview():
    data   = _get_data()
    cycle, lock_meta, _ = _get_cycle()
    dq_r   = _get_dq()
    bases  = _get_baselines()
    segm   = _get_segmentation()
    supply = _get_supply()
    confs  = db_state.load_confirmations(cycle)

    # ── validation classification pass ──────────────────────────────────────
    exc_summary: dict[str, int] = {}
    owner_bias_map = val.owner_bias(data, bases)
    for sid, sku in data["skus"].items():
        b   = bases[sid]
        cls = val.classify(
            sku["projection"], b["baseline"], sku["pending_soc"], sku["lms"],
            segm["abc_xyz"][sid]["cell"], b["intermittent"],
        )
        t = cls.get("type", "Auto-accept")
        exc_summary[t] = exc_summary.get(t, 0) + 1

    open_exc = sum(v for k, v in exc_summary.items() if k != "Auto-accept")

    # ── DQ summary ──────────────────────────────────────────────────────────
    dq_sum = dq_r.get("summary", {})
    dq_scores = dq_r.get("scores", {})

    # ── governance alerts ────────────────────────────────────────────────────
    val_r = val.build_validation(data, bases, segm)
    gov   = governance.build_governance(data, dq_r, val_r, supply, lock_meta is not None)
    alert_tiers = gov.get("alert_tiers", {"escalation": 0, "action": 0, "info": 0})
    gates        = gov.get("gates", [])

    # ── supply summary ───────────────────────────────────────────────────────
    sup_sum = supply.get("summary", {})
    rccp    = supply.get("rccp", [])

    # ── pipeline steps ───────────────────────────────────────────────────────
    pipeline = [
        "1. Portfolio review",
        "2. Demand validation",
        "3. Consensus lock",
        "4. Supply / RM plan",
        "5. RCCP",
        "6. S&OP review",
    ]

    # ── counts ───────────────────────────────────────────────────────────────
    families = {s["family"] for s in data["skus"].values()}

    return {
        # fields used by App.jsx header pills
        "cycle": {
            "cycle_period":    cycle,
            "step":            "Consensus" if lock_meta else "Review",
            "locked":          lock_meta is not None,
            "exceptions_open": open_exc,
            "confirmed":       len(confs),
            "total_skus":      len(data["skus"]),
            "dq_blocked":      len(dq_r.get("blocked_skus", [])),
        },
        # fields used by Overview.jsx
        "pipeline":          pipeline,
        "exception_summary": exc_summary,
        "owner_bias":        owner_bias_map,
        "supply_summary":    sup_sum,
        "rccp":              rccp,
        "counts":            {"skus": len(data["skus"]), "families": len(families)},
        "dq":                dq_sum,
        "dq_scores":         dq_scores,
        "alert_tiers":       alert_tiers,
        "gates":             gates,
        # meta
        "source":            data.get("_source", "synthetic"),
        "scope":             data.get("_scope"),
        "load_warnings":     data.get("_load_warnings", []),
    }


@router.get("/api/dq")
def get_dq():
    return _get_dq()


@router.get("/api/validation")
def get_validation():
    data  = _get_data()
    bases = _get_baselines()
    segm  = _get_segmentation()
    cycle, lock_meta, _ = _get_cycle()
    confs = db_state.load_confirmations(cycle)
    result = val.build_validation(data, bases, segm)
    result["confirmations"] = confs
    result["locked"] = lock_meta is not None
    return result


@router.post("/api/validation/confirm")
def confirm_sku(body: ConfirmBody):
    cycle, lock_meta, _ = _get_cycle()
    if lock_meta:
        raise HTTPException(400, "Cycle is locked — unlock first.")
    if body.sku not in _get_data()["skus"]:
        raise HTTPException(404, f"SKU {body.sku} not found.")
    conf = {"confirmed_qty": body.confirmed_qty, "note": body.note,
            "ts": datetime.utcnow().isoformat()}
    db_state.save_confirmation(cycle, body.sku, conf)
    db_state.append_audit(cycle, {"action": "confirm", "sku": body.sku,
                                  "qty": body.confirmed_qty, "note": body.note,
                                  "ts": conf["ts"]})
    return {"ok": True}


@router.post("/api/validation/lock")
def lock_cycle(body: LockBody):
    cycle, lock_meta, _ = _get_cycle()
    data  = _get_data()
    bases = _get_baselines()
    confs = db_state.load_confirmations(cycle)
    if body.action == "lock":
        if lock_meta:
            raise HTTPException(400, "Already locked.")
        consensus = {sid: confs[sid]["confirmed_qty"] if sid in confs
                     else bases[sid]["baseline"] for sid in data["skus"]}
        meta = {"locked_at": datetime.utcnow().isoformat(), "by": "planner"}
        db_state.set_lock(cycle, meta, consensus)
        db_state.append_audit(cycle, {"action": "lock", **meta})
    else:
        if not lock_meta:
            raise HTTPException(400, "Not locked.")
        db_state.clear_lock(cycle)
        db_state.append_audit(cycle, {"action": "unlock", "ts": datetime.utcnow().isoformat()})
    return {"ok": True}


@router.get("/api/forecasting")
def get_forecasting():
    return fc.build_forecasting(_get_data(), _get_baselines())


@router.get("/api/segmentation")
def get_segmentation():
    return _get_segmentation()


@router.get("/api/confirmations")
def get_confirmations():
    data, bases, segm = _get_data(), _get_baselines(), _get_segmentation()
    cycle, lock_meta, _ = _get_cycle()
    confs = db_state.load_confirmations(cycle)
    rows = []
    for r in val.build_validation(data, bases, segm)["rows"]:
        sku = r["sku"]
        cand = r.get("candidate") or 0.0
        saved = confs.get(sku)
        if saved:
            conf = {"quantity": saved.get("confirmed_qty", saved.get("quantity", cand)),
                    "reason_code": saved.get("reason_code"), "note": saved.get("note"),
                    "status": "confirmed", "owner": r.get("owner")}
        else:
            conf = {"quantity": cand, "reason_code": None, "note": None,
                    "status": "auto-accepted" if r.get("within_band") else "open",
                    "owner": r.get("owner")}
        rows.append({**r, "candidate": cand, "confirmation": conf})
    return {"cycle_period": cycle, "rows": rows, "reason_codes": REASON_CODES,
            "locked": lock_meta is not None}


@router.post("/api/confirmations/{sku}")
def post_confirmation(sku: str, body: ConfirmationBody):
    cycle, lock_meta, _ = _get_cycle()
    if lock_meta:
        raise HTTPException(400, "Consensus is locked; unlock first.")
    if sku not in _get_data()["skus"]:
        raise HTTPException(404, "Unknown SKU")
    conf = {"confirmed_qty": body.quantity, "quantity": body.quantity,
            "reason_code": body.reason_code, "note": body.note, "status": "confirmed",
            "ts": datetime.utcnow().isoformat()}
    db_state.save_confirmation(cycle, sku, conf)
    db_state.append_audit(cycle, {"action": "confirm", "sku": sku, "qty": body.quantity, "ts": conf["ts"]})
    return {"ok": True, "confirmation": conf}


@router.post("/api/consensus/lock")
def post_consensus_lock(body: ActorBody):
    cycle, _, _ = _get_cycle()
    data, bases, segm = _get_data(), _get_baselines(), _get_segmentation()
    confs = db_state.load_confirmations(cycle)
    consensus = {}
    for r in val.build_validation(data, bases, segm)["rows"]:
        sku, cand = r["sku"], (r.get("candidate") or 0.0)
        saved = confs.get(sku)
        consensus[sku] = saved.get("confirmed_qty", cand) if saved else cand
    meta = {"locked_at": datetime.utcnow().isoformat(), "by": body.actor}
    db_state.set_lock(cycle, meta, consensus)
    db_state.append_audit(cycle, {"action": "lock", **meta})
    return {"ok": True, "lock_meta": meta}


@router.post("/api/consensus/unlock")
def post_consensus_unlock(body: ActorBody):
    cycle, _, _ = _get_cycle()
    db_state.clear_lock(cycle)
    db_state.append_audit(cycle, {"action": "unlock", "ts": datetime.utcnow().isoformat()})
    return {"ok": True}


@router.get("/api/skus/{sku}/history")
def get_sku_history(sku: str):
    data = _get_data()
    if sku not in data["skus"]:
        raise HTTPException(404, "Unknown SKU")
    s = data["skus"][sku]
    return {"sku": sku, "name": s["name"], "family": s["family"],
            "history": data["history"][sku], "baseline": _get_baselines()[sku],
            "projection": s.get("projection"), "pending_soc": s.get("pending_soc"),
            "lms": s.get("lms"), "cycle_period": data["cycle_period"]}


@router.post("/api/what-if")
def post_what_if(body: WhatIfBody):
    fn = getattr(analytics, "what_if", None)
    if fn is None:
        return {"note": "What-if scenario engine not available in this build.",
                "scenario": body.model_dump()}
    try:
        return fn(_get_data(), _get_baselines(), _get_segmentation(),
                  _get_consensus(), body.model_dump(), set())
    except Exception as e:   # noqa: BLE001
        return {"note": f"What-if unavailable: {type(e).__name__}", "scenario": body.model_dump()}


@router.get("/api/jc-plan")
def get_jc_plan():
    return build_jc_plan(_get_data(), _get_baselines(), _get_segmentation())


@router.get("/api/analytics")
def get_analytics():
    return analytics.build_analytics(
        _get_data(), _get_baselines(), _get_segmentation(), _get_supply())


@router.get("/api/kpis")
def get_kpis():
    data   = _get_data()
    bases  = _get_baselines()
    segm   = _get_segmentation()
    supply = _get_supply()
    val_r  = val.build_validation(data, bases, segm)
    return kpis.build_kpis(data, bases, segm, supply, val_r)


@router.get("/api/governance")
def get_governance():
    data   = _get_data()
    bases  = _get_baselines()
    segm   = _get_segmentation()
    supply = _get_supply()
    dq_r   = _get_dq()
    _, lock_meta, _ = _get_cycle()
    val_r  = val.build_validation(data, bases, segm)
    return governance.build_governance(data, dq_r, val_r, supply, lock_meta is not None)


@router.get("/api/audit")
def get_audit():
    cycle, _, _ = _get_cycle()
    return {"cycle": cycle, "entries": db_state.load_audit(cycle)}
