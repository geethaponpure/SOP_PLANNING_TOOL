"""My Dashboard page (permission-scoped charts)."""
from ._deps import *
from ..api.dashboard import my_dashboard, persona_users

router = APIRouter()


@router.get("/api/my-dashboard")
def get_my_dashboard(username: str = "", email: str = "", admin: int = 0,
                     persona: str = ""):
    return my_dashboard(username=username or None, email=email or None,
                        admin=bool(admin), persona=persona or None)


@router.get("/api/my-dashboard/personas")
def get_dashboard_personas():
    """Persona -> mapped users list for the admin 'View as' switcher."""
    return persona_users()
