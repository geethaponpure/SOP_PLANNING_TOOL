"""Projection vs Sales, and Projection Accuracy (projection vs actual production)."""
from ._deps import *

router = APIRouter()


@router.get("/api/projection-sales")
@router.get("/api/projection-vs-sales")
def get_projection_sales():
    return _proj_sales_live()


@router.get("/api/projection-sales/export")
@router.get("/api/projection-vs-sales/export")
def export_projection_sales():
    return _xlsx(_pub.build_projection_sales_workbook(_proj_sales_live(), _live_cycle()),
                 "Projection_vs_Sales.xlsx")


@router.get("/api/projection-accuracy/meta")
def projection_accuracy_meta():
    idx = _consump_index()
    return {"years": [{"acc_year": y, "jcs": sorted(j for j in idx[y] if j > 0),
                       "has_full": 0 in idx[y]} for y in sorted(idx, reverse=True)]}


@router.get("/api/projection-accuracy")
def get_projection_accuracy(acc_year: str | None = None, jc: int | None = None,
                            approved: bool = False):
    return _proj_accuracy(acc_year, jc, approved)


@router.get("/api/projection-accuracy/export")
def export_projection_accuracy(acc_year: str | None = None, jc: int | None = None,
                               approved: bool = False):
    rp = _proj_accuracy(acc_year, jc, approved)
    sc = rp.get("scope", {})
    fn = f"Projection_Accuracy_{sc.get('acc_year', '')}_{sc.get('label', '').replace(' ', '')}.xlsx"
    return _xlsx(_pub.build_projection_accuracy_workbook(rp, _live_cycle()), fn)
