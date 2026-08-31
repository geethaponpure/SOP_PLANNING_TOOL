"""Supplier Scorecard + Purchase Price Variance (procurement analytics)."""
from ._deps import *

router = APIRouter()


@router.get("/api/supplier-scorecard")
def get_supplier_scorecard():
    return _scorecard_live()


@router.get("/api/supplier-scorecard/export")
def export_supplier_scorecard():
    return _xlsx(_pub.build_supplier_scorecard_workbook(_scorecard_live(), _live_cycle()),
                 "Supplier_Scorecard.xlsx")


@router.get("/api/ppv")
def get_ppv():
    return _ppv_live()


@router.get("/api/ppv/export")
def export_ppv():
    return _xlsx(_pub.build_ppv_workbook(_ppv_live(), _live_cycle()), "PPV_Scorecard.xlsx")
