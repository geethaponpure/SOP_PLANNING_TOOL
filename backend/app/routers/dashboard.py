"""My Dashboard page (permission-scoped charts)."""
from ._deps import *
from ..api.dashboard import item_detail, my_dashboard, persona_users

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
