"""Reader + aggregation for the MFG Raw-Material Consumption exports (RM_Consumption/).

These are Oracle BI Publisher HTML tables saved with an .xls extension (one <tr> per
job x raw-material line). Columns we care about:

    Org Name | Mfg Item Code | Item Description | Item Category | Product Group |
    Product Category | Product Sub Category | Uom | Output Quantity | Lot Number |
    Job Number | Job Creation Date | Rw Item Code | Item Description R | Uom R |
    Qty Consumed | Rate Per Unit | Value Of Raw Material

ACTUAL PRODUCTION = the *Output Quantity of each unique Job Number* (the value repeats
across a job's RM lines, so it is counted once per job). Files are laid out one per JC:
  RM_Consumption/<acc_year_folder>/Pure_MFG_..._<ddmmyy>[-_]JC<n>.xls
(the 2024_25 folder holds a single full-year dump with no JC tag).
"""
from __future__ import annotations

import glob
import hashlib
import html as _html
import os
import pickle
import re

from .planning_filter import _norm, _num, _squash

_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")

# columns the aggregation needs (a subset of the 18 exported)
_KEEP = ("Org Name", "Mfg Item Code", "Item Description", "Item Category",
         "Product Group", "Product Category", "Product Sub Category", "Uom",
         "Output Quantity", "Job Number", "Job Creation Date")


def consumption_root() -> str:
    """Absolute path to the RM_Consumption/ folder (repo root / RM_Consumption).
    Overridable with the RM_CONSUMPTION_DIR environment variable."""
    env = os.getenv("RM_CONSUMPTION_DIR")
    if env:
        return env
    # backend/app/integration/rm_consumption.py -> repo root is three levels up
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    # Prefer the data-ingestion folder (Data_Ingestion/RM_Consumption) if present,
    # else fall back to the project root -- same DATA_DIR convention as the adapter.
    data_dir = os.getenv("DATA_DIR", "").strip() or os.path.join(root, "Data_Ingestion")
    in_data = os.path.join(data_dir, "RM_Consumption")
    if os.path.isdir(in_data):
        return in_data
    return os.path.join(root, "RM_Consumption")


def _acc_year_from_folder(folder: str) -> str:
    """'2026_27' -> '2026-2027' (the CRM acc_year format)."""
    m = re.match(r"(\d{4})[_-](\d{2,4})", folder)
    if not m:
        return folder
    a = int(m.group(1))
    return f"{a}-{a + 1}"


def _jc_of(fname: str) -> int:
    m = re.search(r"JC(\d+)", fname, re.I)
    return int(m.group(1)) if m else 0


def _dlseq(fname: str) -> str:
    """A sortable key from the ddmmyy stamp in the filename (later download wins when
    two files carry the same JC). Falls back to the filename itself."""
    m = re.search(r"_(\d{2})(\d{2})(\d{2})[-_]?", fname)
    if m:
        return m.group(3) + m.group(2) + m.group(1)   # yymmdd
    return fname


def discover(root: str | None = None) -> dict[str, dict[int, dict]]:
    """{acc_year: {jc_number: {'path','fname','jc'}}}. JC 0 = untagged full-year dump.
    When several files share an (acc_year, jc), the newest by filename date is kept."""
    root = root or consumption_root()
    out: dict[str, dict[int, dict]] = {}
    if not os.path.isdir(root):
        return out
    for folder in sorted(os.listdir(root)):
        fdir = os.path.join(root, folder)
        if not os.path.isdir(fdir):
            continue
        acc = _acc_year_from_folder(folder)
        for path in glob.glob(os.path.join(fdir, "*.xls")) + glob.glob(os.path.join(fdir, "*.htm*")):
            fname = os.path.basename(path)
            jc = _jc_of(fname)
            slot = out.setdefault(acc, {})
            cur = slot.get(jc)
            if cur is None or _dlseq(fname) >= _dlseq(cur["fname"]):
                slot[jc] = {"path": path, "fname": fname, "jc": jc, "folder": folder}
    return out


def _iter_rows(path: str):
    """Stream {col: value} dicts for the kept columns from one HTML export."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    ix = None
    for m in _ROW.finditer(content):
        vals = [_html.unescape(_TAG.sub("", c)).strip() for c in _CELL.findall(m.group(1))]
        if ix is None:
            if "Mfg Item Code" in vals and "Output Quantity" in vals:
                ix = {n: i for i, n in enumerate(vals)}
            continue
        if "Mfg Item Code" in vals:      # repeated header on a later page
            continue
        yield {c: (vals[ix[c]] if c in ix and ix[c] < len(vals) else None) for c in _KEEP}


def jobs_of_file(path: str) -> dict[str, dict]:
    """{job_number: {item_key,item_desc,item_code,org,division,product,subcat,uom,output}}
    Output Quantity counted once per job. Cached to disk by file mtime."""
    cache_file = None
    try:
        key = hashlib.md5(f"{path}:{os.path.getmtime(path)}:v1".encode()).hexdigest()
        cdir = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
        os.makedirs(cdir, exist_ok=True)
        cache_file = os.path.join(cdir, f"rmconsump_{key}.pkl")
        if os.path.exists(cache_file):
            with open(cache_file, "rb") as f:
                return pickle.load(f)
    except OSError:
        cache_file = None
    jobs: dict[str, dict] = {}
    for r in _iter_rows(path):
        jn = _norm(r.get("Job Number"))
        if not jn or jn in jobs:
            continue
        desc = _norm(r.get("Item Description"))
        jobs[jn] = {
            "item_key": _squash(desc),
            "item_desc": desc,
            "item_code": _norm(r.get("Mfg Item Code")),
            "org": _norm(r.get("Org Name")),
            "division": _norm(r.get("Product Group")),
            "product": _norm(r.get("Product Category")),
            "subcat": _norm(r.get("Product Sub Category")),
            "category": _norm(r.get("Item Category")),
            "uom": _norm(r.get("Uom")),
            "output": _num(r.get("Output Quantity")),
            "job_date": _norm(r.get("Job Creation Date")),
        }
    if cache_file:
        try:
            with open(cache_file, "wb") as f:
                pickle.dump(jobs, f, protocol=pickle.HIGHEST_PROTOCOL)
        except OSError:
            pass
    return jobs


def rm_consumed_of_file(path: str) -> dict[str, dict]:
    """{squash(rm_desc): {'rm_desc','rm_code','qty'}} = total 'Qty Consumed' per raw
    material in one JC file (summed across every job x RM line). This is the RM INPUT
    side (Rw Item Code / Item Description R / Qty Consumed), used for the Aged-RM report."""
    out: dict[str, dict] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    ix = None
    for m in _ROW.finditer(content):
        vals = [_html.unescape(_TAG.sub("", c)).strip() for c in _CELL.findall(m.group(1))]
        if ix is None:
            if "Rw Item Code" in vals and "Qty Consumed" in vals:
                ix = {n: i for i, n in enumerate(vals)}
            continue
        if "Rw Item Code" in vals:          # repeated header on a later page
            continue

        def _g(col):
            i = ix.get(col)
            return vals[i] if i is not None and i < len(vals) else None
        desc = _norm(_g("Item Description R"))
        code = _norm(_g("Rw Item Code"))
        qty = _num(_g("Qty Consumed"))
        k = _squash(desc) or code
        if not k or qty == 0:
            continue
        a = out.setdefault(k, {"rm_desc": desc, "rm_code": code, "qty": 0.0})
        a["qty"] += qty
    for a in out.values():
        a["qty"] = round(a["qty"], 1)
    return out


def rm_consumed_by_jc(acc_year: str, jc_numbers) -> list:
    """[(jc_number, {squash(rm_desc): {...,'qty'}})] for the requested JC numbers of an
    accounting year. A JC with no consumption file yields an empty map."""
    slot = discover().get(acc_year, {})
    out = []
    for jc in jc_numbers:
        info = slot.get(jc)
        out.append((jc, rm_consumed_of_file(info["path"]) if info else {}))
    return out


def production_by_item(paths) -> dict[str, dict]:
    """Merge one or more consumption files into {item_key: aggregate}. Jobs are unique
    across files (dict update), so a job appearing in overlapping downloads is not
    double-counted. Aggregate carries produced Output Qty + the item's division/product
    hierarchy (from the item's most-recent job)."""
    if isinstance(paths, str):
        paths = [paths]
    jobs: dict[str, dict] = {}
    for p in paths:
        if p:
            jobs.update(jobs_of_file(p))
    agg: dict[str, dict] = {}
    for rec in jobs.values():
        k = rec["item_key"]
        if not k:
            continue
        a = agg.get(k)
        if a is None:
            a = agg[k] = {"item_key": k, "item_desc": rec["item_desc"],
                          "item_code": rec["item_code"], "org": rec["org"],
                          "division": rec["division"], "product": rec["product"],
                          "subcat": rec["subcat"], "category": rec["category"],
                          "uom": rec["uom"], "actual": 0.0, "jobs": 0}
        a["actual"] += rec["output"]
        a["jobs"] += 1
        # keep the richest hierarchy labels seen for the item
        for f in ("division", "product", "subcat", "category", "item_code", "org"):
            if not a[f] and rec[f]:
                a[f] = rec[f]
    for a in agg.values():
        a["actual"] = round(a["actual"], 1)
    return agg
