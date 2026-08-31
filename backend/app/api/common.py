"""Small shared helpers used across the service layer and routers."""
from __future__ import annotations

import io
import os
from datetime import date as _date

from fastapi import HTTPException
from fastapi.responses import StreamingResponse


def _live() -> bool:
    return os.getenv("DATA_SOURCE", "synthetic").lower() == "live"


def _try(fn, label):
    try:
        return fn()
    except Exception as e:   # noqa: BLE001
        print(f"[live] {label}: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
        return None


def _months_ago(d, n):
    """Date n whole months before d (day clamped to 28 to stay valid)."""
    m, y = d.month - int(n), d.year
    while m <= 0:
        m += 12
        y -= 1
    return _date(y, m, min(d.day, 28))


def _xlsx(data: bytes, name: str) -> StreamingResponse:
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _excel_response(result: dict, sheet_name: str) -> StreamingResponse:
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(500, "openpyxl not installed.")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Key", "Value"])
    for k, v in result.get("summary", {}).items():
        ws.append([str(k), "" if v is None else str(v)])

    for key in ("products", "consolidated_rm", "items", "suppliers", "jc_performance"):
        rows = result.get(key, [])
        if not rows:
            continue
        ws2 = wb.create_sheet(key.replace("_", " ").title()[:31])
        flat = [{k: v for k, v in r.items() if not isinstance(v, (list, dict))} for r in rows]
        if flat:
            ws2.append(list(flat[0].keys()))
            for r in flat:
                ws2.append(["" if v is None else str(v) for v in r.values()])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{sheet_name}.xlsx"'},
    )


__all__ = [n for n, v in list(globals().items())
           if callable(v) and getattr(v, "__module__", None) == __name__ and not n.startswith("__")]
