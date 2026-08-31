"""Aged RM -> FG and the aged-RM excess report."""
from ._deps import *

router = APIRouter()


@router.get("/api/aged-rm")
@router.get("/api/aged-rm-plan")
def get_aged_rm():
    return _aged_rm()


@router.get("/api/aged-rm/export")
@router.get("/api/aged-rm-plan/export")
def export_aged_rm():
    return _xlsx(_pub.build_aged_rm_workbook(_aged_rm(), _live_cycle()), "Aged_RM_Plan.xlsx")


@router.get("/api/aged-rm/report")
def get_aged_rm_report():
    return _aged_rm_report()


@router.get("/api/aged-rm/report-export")
def export_aged_rm_report():
    return _xlsx(_pub.build_aged_rm_report_workbook(_aged_rm_report(), _live_cycle()),
                 "Report_Aged_RM.xlsx")
