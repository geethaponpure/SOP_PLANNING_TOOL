r"""Schema-discovery probe.

Run this ONCE on a machine inside the client network (it needs to reach CRMPROD
@ 10.1.0.146:1433). It connects, runs each CRM source, and prints the column
names + a few sample rows -- so the exact field mapping in ``adapter.py`` can be
confirmed against the real data.

    cd backend
    .\.venv\Scripts\python.exe -m app.integration.probe          # all sources
    .\.venv\Scripts\python.exe -m app.integration.probe pto_pts  # one source

Copy the output back so the mapping can be finalised. (No data is written
anywhere; this is read-only.)
"""
from __future__ import annotations

import sys

from . import crm_sources


def _preview(name: str, fn, sample: int = 3) -> None:
    print("=" * 78)
    print(f"SOURCE: {name}")
    print("-" * 78)
    try:
        rows = fn()
    except Exception as e:  # noqa: BLE001 -- surface any connection/SQL error plainly
        print(f"  ERROR: {type(e).__name__}: {e}")
        return
    if not rows:
        print("  (0 rows returned)")
        return
    cols = list(rows[0].keys())
    print(f"  rows: {len(rows)}   columns ({len(cols)}):")
    for c in cols:
        print(f"    - {c}")
    print(f"  first {min(sample, len(rows))} row(s):")
    for r in rows[:sample]:
        print("    " + " | ".join(f"{k}={r[k]!r}" for k in cols))


def main() -> None:
    targets = sys.argv[1:] or list(crm_sources.SOURCES)
    for name in targets:
        if name not in crm_sources.SOURCES:
            print(f"Unknown source '{name}'. Known: {', '.join(crm_sources.SOURCES)}")
            continue
        # use the TOP-N sample query where available (dispatch/quote can be huge)
        _preview(name, lambda n=name: crm_sources.probe_source(n))
    print("=" * 78)
    print("Done. Copy this output back to finalise the column mapping in adapter.py")


if __name__ == "__main__":
    main()
