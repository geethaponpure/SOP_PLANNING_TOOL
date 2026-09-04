"""My Dashboard page (permission-scoped charts)."""
from ._deps import *
from ..api import commit_export as _cx
from ..api.commit import commit_risk, scoped_rows
from ..api.dashboard import item_detail, my_dashboard, persona_users
from ..api import dashboard_export as _dx

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
