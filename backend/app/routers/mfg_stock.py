"""MFG Org Stock page."""
from ._deps import *

router = APIRouter()


@router.get("/api/mfg-stock")
def get_mfg_stock():
    return _mfg_stock()
