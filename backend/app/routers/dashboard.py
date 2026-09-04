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


@router.get("/api/dashboard-layout")
def get_dashboard_layout(key: str = "mydash"):
    """The admin-saved default card arrangement (empty when none is saved)."""
    return {"key": key, "layouts": staging.read_ui_layout(key) or None}


@router.put("/api/dashboard-layout")
def put_dashboard_layout(body: LayoutIn):
    """Store the current arrangement as the default everyone starts from."""
    staging.save_ui_layout(body.key, body.layouts)
    return {"ok": True}


@router.get("/api/my-dashboard/personas")
def get_dashboard_personas():
    """Persona -> mapped users list for the admin 'View as' switcher."""
    return persona_users()
