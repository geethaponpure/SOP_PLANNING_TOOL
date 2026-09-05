"""My Dashboard page (permission-scoped charts)."""
from ._deps import *
from ..api import commit_export as _cx
from ..api.commit import commit_risk, scoped_rows
from ..api.dashboard import item_detail, my_dashboard, persona_users
from ..api import dashboard_export as _dx
from ..api import demand_export as _mx
from ..api.demand import demand_protection, scoped_ledger
from ..api import competition_export as _cpx
from ..api.competition import item_competition, supply_competition
from ..api.competition import scoped_ledger as _comp_ledger
from ..api import promise_export as _prx
from ..api.promise import item_timeline, promise_dates
from ..api.promise import scoped_rows as _promise_rows
from ..api import action_export as _abx
from ..api.action_board import action_board, item_supply
from ..api.action_board import scoped_rows as _action_rows

router = APIRouter()


@router.get("/api/my-dashboard/item")
def get_dashboard_item(item: str = "", code: str = "", username: str = "",
                       email: str = "", admin: int = 0, persona: str = ""):
    """Per-JC dispatch + projection for one item (the click-to-drill popup)."""
    return item_detail(username=username or None, email=email or None,
                       admin=bool(admin), persona=persona or None,
                       item=item, code=code or None)


@router.get("/api/my-dashboard")
def get_my_dashboard(username: str = "", email: str = "", admin: int = 0,
                     persona: str = ""):
    return my_dashboard(username=username or None, email=email or None,
                        admin=bool(admin), persona=persona or None)


class LayoutIn(BaseModel):
    key: str = "mydash"
    layouts: dict
    user: str = ""      # empty = write the APP-LEVEL default (admin action)


def _user_key(key: str, user: str) -> str:
    return f"{key}:{(user or '').strip().lower()}"


@router.get("/api/my-dashboard/export")
def export_my_dashboard(username: str = "", email: str = "", admin: int = 0,
                        persona: str = "", section: str = ""):
    """Excel of one card's table (``section``) or the whole page (charts + tables),
    always for the caller's own scope."""
    if section and section not in _dx.SECTION_TITLES:
        raise HTTPException(400, f"unknown section '{section}'")
    payload = my_dashboard(username=username or None, email=email or None,
                           admin=bool(admin), persona=persona or None)
    data = _dx.build(payload, section or None)
    who = (payload.get("persona") or "dashboard").replace(" ", "_")
    name = f"{_dx.SECTION_TITLES[section].replace(' ', '_')}_{who}.xlsx" if section         else f"My_Dashboard_{who}.xlsx"
    return _xlsx(data, name)


@router.get("/api/commit-risk")
def get_commit_risk(username: str = "", email: str = "", admin: int = 0,
                    persona: str = ""):
    """The Commitment-Risk page payload, scoped exactly like /api/my-dashboard."""
    return commit_risk(username=username or None, email=email or None,
                       admin=bool(admin), persona=persona or None)


@router.get("/api/commit-risk/export")
def export_commit_risk(username: str = "", email: str = "", admin: int = 0,
                       persona: str = "", section: str = ""):
    """Excel of one Commitment-Risk card, or the whole page (charts + tables)."""
    if section and section not in _cx.SECTION_TITLES:
        raise HTTPException(400, f"unknown section '{section}'")
    payload = commit_risk(username=username or None, email=email or None,
                          admin=bool(admin), persona=persona or None)
    _p, _s, _m, rows = scoped_rows(username=username or None, email=email or None,
                                   admin=bool(admin), persona=persona or None)
    data = _cx.build(payload, rows, section or None)
    who = (payload.get("persona") or "commit").replace(" ", "_")
    name = f"{_cx.SECTION_TITLES[section].replace(' ', '_')}_{who}.xlsx" if section         else f"Commitment_Risk_{who}.xlsx"
    return _xlsx(data, name)


@router.get("/api/demand-protection")
def get_demand_protection(username: str = "", email: str = "", admin: int = 0,
                          persona: str = "", jc: int = 0):
    """Projection vs firm demand for one JC, scoped exactly like /api/my-dashboard.
    ``jc`` defaults to the planning JC."""
    return demand_protection(username=username or None, email=email or None,
                             admin=bool(admin), persona=persona or None,
                             jc=int(jc) or None)


@router.get("/api/demand-protection/export")
def export_demand_protection(username: str = "", email: str = "", admin: int = 0,
                             persona: str = "", jc: int = 0, section: str = ""):
    """Excel of one Demand-Protection card, or the whole page (charts + tables)."""
    if section and section not in _mx.SECTION_TITLES:
        raise HTTPException(400, f"unknown section '{section}'")
    payload = demand_protection(username=username or None, email=email or None,
                                admin=bool(admin), persona=persona or None,
                                jc=int(jc) or None)
    _p, _s, _m, _ay, _jj, rows = scoped_ledger(username=username or None,
                                               email=email or None, admin=bool(admin),
                                               persona=persona or None, jc=int(jc) or None)
    data = _mx.build(payload, rows, section or None)
    who = (payload.get("persona") or "demand").replace(" ", "_")
    cyc = payload.get("jc_label") or ""
    name = f"{_mx.SECTION_TITLES[section].replace(' ', '_')}_{cyc}_{who}.xlsx" if section \
        else f"Demand_Protection_{cyc}_{who}.xlsx"
    return _xlsx(data, name)


@router.get("/api/supply-competition")
def get_supply_competition(username: str = "", email: str = "", admin: int = 0,
                           persona: str = "", jc: int = 0):
    """Per-item supply position and competing firm demand, scoped like
    /api/my-dashboard. ``jc`` defaults to the planning JC."""
    return supply_competition(username=username or None, email=email or None,
                              admin=bool(admin), persona=persona or None,
                              jc=int(jc) or None)


@router.get("/api/supply-competition/item")
def get_supply_competition_item(item: str = "", username: str = "", email: str = "",
                                admin: int = 0, persona: str = "", jc: int = 0):
    """One item's full picture: my requirement, the supply position, and who
    holds the competing commitments (names only within the caller's scope)."""
    if not item:
        raise HTTPException(400, "item is required")
    return item_competition(item, username=username or None, email=email or None,
                            admin=bool(admin), persona=persona or None,
                            jc=int(jc) or None)


@router.get("/api/supply-competition/export")
def export_supply_competition(username: str = "", email: str = "", admin: int = 0,
                              persona: str = "", jc: int = 0, section: str = ""):
    """Excel of one Supply-Competition card, or the whole page."""
    if section and section not in _cpx.SECTION_TITLES:
        raise HTTPException(400, f"unknown section '{section}'")
    payload = supply_competition(username=username or None, email=email or None,
                                 admin=bool(admin), persona=persona or None,
                                 jc=int(jc) or None)
    _p, _s, _m, _ay, _jj, rows = _comp_ledger(username=username or None,
                                              email=email or None, admin=bool(admin),
                                              persona=persona or None, jc=int(jc) or None)
    data = _cpx.build(payload, rows, section or None)
    who = (payload.get("persona") or "supply").replace(" ", "_")
    cyc = payload.get("jc_label") or ""
    name = f"{_cpx.SECTION_TITLES[section].replace(' ', '_')}_{cyc}_{who}.xlsx" if section \
        else f"Supply_Competition_{cyc}_{who}.xlsx"
    return _xlsx(data, name)


@router.get("/api/promise-dates")
def get_promise_dates(username: str = "", email: str = "", admin: int = 0,
                      persona: str = "", jc: int = 0):
    """When each item can be promised, and when its stock runs out. Scoped like
    /api/my-dashboard; ``jc`` defaults to the planning JC."""
    return promise_dates(username=username or None, email=email or None,
                         admin=bool(admin), persona=persona or None,
                         jc=int(jc) or None)


@router.get("/api/promise-dates/item")
def get_promise_item(item: str = "", username: str = "", email: str = "",
                     admin: int = 0, persona: str = "", jc: int = 0):
    """One item's supply timeline: every dated source, the orders burning it
    down, and the running balance behind the promise and risk dates."""
    if not item:
        raise HTTPException(400, "item is required")
    return item_timeline(item, username=username or None, email=email or None,
                         admin=bool(admin), persona=persona or None,
                         jc=int(jc) or None)


@router.get("/api/promise-dates/export")
def export_promise_dates(username: str = "", email: str = "", admin: int = 0,
                         persona: str = "", jc: int = 0, section: str = ""):
    """Excel of one Promise-Dates card, or the whole page."""
    if section and section not in _prx.SECTION_TITLES:
        raise HTTPException(400, f"unknown section '{section}'")
    payload, rows = _promise_rows(username=username or None, email=email or None,
                                  admin=bool(admin), persona=persona or None,
                                  jc=int(jc) or None)
    data = _prx.build(payload, rows, section or None)
    who = (payload.get("persona") or "promise").replace(" ", "_")
    cyc = payload.get("jc_label") or ""
    name = f"{_prx.SECTION_TITLES[section].replace(' ', '_')}_{cyc}_{who}.xlsx" if section \
        else f"Promise_Dates_{cyc}_{who}.xlsx"
    return _xlsx(data, name)


@router.get("/api/supply-position")
def get_supply_position(username: str = "", email: str = "", admin: int = 0,
                        persona: str = "", jc: int = 0):
    """My Supply Position — the headline strip plus the customer x item action
    list, scoped like /api/my-dashboard. ``jc`` defaults to the planning JC."""
    return action_board(username=username or None, email=email or None,
                        admin=bool(admin), persona=persona or None,
                        jc=int(jc) or None)


@router.get("/api/supply-position/item")
def get_supply_position_item(item: str = "", username: str = "", email: str = "",
                             admin: int = 0, persona: str = "", jc: int = 0):
    """One item's supply picture behind the board: the dated ladder, the orders
    consuming it, and where the stock sits."""
    if not item:
        raise HTTPException(400, "item is required")
    return item_supply(item, username=username or None, email=email or None,
                       admin=bool(admin), persona=persona or None, jc=int(jc) or None)


@router.get("/api/supply-position/export")
def export_supply_position(username: str = "", email: str = "", admin: int = 0,
                           persona: str = "", jc: int = 0, section: str = ""):
    """Excel of one block, or the whole board."""
    if section and section not in _abx.SECTION_TITLES:
        raise HTTPException(400, f"unknown section '{section}'")
    payload, rows = _action_rows(username=username or None, email=email or None,
                                 admin=bool(admin), persona=persona or None,
                                 jc=int(jc) or None)
    data = _abx.build(payload, rows, section or None)
    who = (payload.get("persona") or "supply").replace(" ", "_")
    cyc = payload.get("jc_label") or ""
    name = f"{_abx.SECTION_TITLES[section].replace(' ', '_')}_{cyc}_{who}.xlsx" if section         else f"My_Supply_Position_{cyc}_{who}.xlsx"
    return _xlsx(data, name)


@router.get("/api/dashboard-layout")
def get_dashboard_layout(key: str = "mydash", user: str = ""):
    """Both layers: the app-level default everyone starts from, and this user's
    own arrangement (which wins for them)."""
    return {
        "key": key,
        "layouts": staging.read_ui_layout(key) or None,
        "user_layouts": (staging.read_ui_layout(_user_key(key, user)) if user else None) or None,
    }


@router.put("/api/dashboard-layout")
def put_dashboard_layout(body: LayoutIn):
    """With ``user``: save that person's own arrangement. Without: set the
    app-level default. An empty ``layouts`` clears the slot."""
    scope = "user" if body.user else "app"
    staging.save_ui_layout(_user_key(body.key, body.user) if body.user else body.key,
                           body.layouts)
    return {"ok": True, "scope": scope}


@router.get("/api/my-dashboard/personas")
def get_dashboard_personas():
    """Persona -> mapped users list for the admin 'View as' switcher."""
    return persona_users()
