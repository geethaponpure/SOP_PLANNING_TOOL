# S&OP / IBP Planning Tool

A working implementation of the **Sales & Operations Planning** tool described in the
*S&OP Planning Tool — Design & Functional Blueprint*. It takes Sales projections (CRM),
validates them against objective demand signals, secures a confirmed accountable forecast,
and converts that consensus into a feasible production and raw-material plan — for a business
that manufactures and trades **chemicals**.

This is an **end-to-end thin slice**: every major module of the blueprint is implemented and
wired together over one cycle of synthetic chemical-SKU data, so the whole loop is demoable.

```
Capture → Baseline → Validate → Confirm → Net & segment → Plan supply & RM → Balance & approve
```

## What's implemented (blueprint section → feature)

| Blueprint | Implemented as |
|---|---|
| §5.1–5.2 Data inputs & master data | Seeded synthetic CRM projection, Pending SOC, LMS feed, 24-mo history, BOM, RM master, capacity assets, batch sizes, quality-release times, supplier delivery history (`backend/app/data.py`) |
| §5.3 Data-Quality gate | Completeness/validity/timeliness scoring; critical defects (missing BOM/lead-time, negative on-hand, stale price) block SKUs/RMs and raise stewardship exceptions; cascades to consuming SKUs (`engine/dq.py`) |
| §7.1 Statistical baseline | History cleansing (stock-out + bulk-deal correction), level/trend/seasonality, Croston for intermittent, method auto-selection (`engine/baseline.py`) |
| §9 Forecasting methods | Per-series method selection **+ hierarchical reconciliation (SKU→family→region→total), champion/challenger back-test, demand sensing** (`engine/forecasting.py`) |
| §7.2–7.4 Validation engine | Triangulation vs baseline/SOC/LMS, segment tolerance bands, SOC floor rule, exception classification, owner bias guardrail (`engine/validation.py`) |
| §8 Confirmation workflow | Reason codes, override discipline, consensus lock with re-approval, append-only audit trail (`store.py`) |
| §6 Segmentation | ABC-XYZ 9-cell, FG PTO/PTS decision, independent RM PTO/PTS + Kraljic quadrant (`engine/segmentation.py`) |
| §10.1–10.3 Supply & RM planning | Netting, statistical safety stock + safety lead time by service level, BOM explosion (yield/scrap), RM netting, rough-cut capacity check (`engine/supply.py`) |
| §10.4 Chemical constraints | **Lot-sizing to whole batch/min-run, MOQ-rounded ordering, shelf-life caps on PTS holding, quality-release lead time, co-/by-product joint planning** (`engine/supply.py`) |
| §11 KPI framework | Back-tested WMAPE/bias/FVA + service/inventory/RM KPIs with targets & owners (`engine/kpis.py`) |
| §12 Analytics & intelligence | **Projection anomaly detection, stock-out/expiry risk scoring, supplier-reliability prediction, what-if scenario simulation, prescriptive MEIO/optimal-buy, segmentation auto-tuning** (`engine/analytics.py`) |
| §13 Collaboration & §14 governance | **RACI, tiered alert inbox (FYI/action/escalation), approval thresholds, communication cadence, gated 5-step S&OP/IBP cadence** (`engine/governance.py`) + exception inbox, S&OP cockpit, audit trail (React frontend) |
| §16 Risks | Risks & mitigations register surfaced in the Governance page |

## Architecture

- **Backend** — Python **FastAPI**, pure-stdlib planning engine (no native deps; runs on Python 3.14).
- **Frontend** — **React + Vite**, Recharts for charts.
- **State** — in-memory single system of record for one cycle (reset any time).

```
sop-planning-tool/
├─ backend/
│  ├─ app/
│  │  ├─ data.py            # synthetic CRM/ERP/LMS feeds + master data
│  │  ├─ store.py           # cycle state, confirmations, lock, audit trail
│  │  ├─ main.py            # FastAPI routes
│  │  └─ engine/            # dq · baseline · forecasting · validation · segmentation · supply · analytics · kpis · governance
│  ├─ run.py                # python run.py  -> :8000
│  └─ requirements.txt
└─ frontend/
   └─ src/pages/            # Cockpit · DataQuality · Validation · Forecasting · Segmentation · Supply · Analytics · KPIs · Governance · Audit
```

## Run it

### 1. Backend (Python 3.11+; tested on 3.14)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py                       # serves http://localhost:8000  (docs at /docs)
```

### 2. Frontend (requires Node 18+)

```powershell
cd frontend
npm install
npm run dev                         # serves http://localhost:5173  (proxies /api -> :8000)
```

Open **http://localhost:5173**. (Node is not bundled — install from https://nodejs.org if needed.
The backend alone is fully usable via its REST API and the Swagger UI at `/docs`.)

## Connecting to live data (CRM + Oracle)

By default the tool runs on the seeded synthetic dataset. To point it at the real
**CRM (SQL Server, CRMPROD)** and **Oracle staging** feeds, the data layer under
`backend/app/integration/` swaps in — the planning engine is unchanged.

1. `pip install -r requirements.txt` (adds `pyodbc`, `oracledb`, `python-dotenv`).
2. `copy backend\.env.example backend\.env` and fill in the CRM password (and
   Oracle creds when available). Set `DATA_SOURCE=live`. **`.env` is gitignored —
   never commit credentials.**
3. **Run the schema probe inside the client network** (it must reach
   `10.1.0.146:1433`) to capture the real column names:
   ```powershell
   cd backend
   .\.venv\Scripts\python.exe -m app.integration.probe
   ```
   This is read-only; it prints each source's columns + sample rows.
4. Confirm/adjust the column mapping in `integration/adapter.py` (`COLUMN_MAP`)
   from the probe output, then start the backend as usual.

Source → engine mapping: PTO/PTS query → item master + PTO/PTS flag (columns
confirmed); dispatch details → shipment history → baseline; business plan →
projection; SOC pending → firm floor; quotes → independent 3rd signal; Oracle
stock/BOM/PO → on-hand / BOM / open-PO. With `LIVE_DEMAND_ONLY=yes` the demand
side runs on real CRM data while RM/BOM/capacity stay as placeholders until
Oracle staging is wired.

**Built for real-world conditions:**
- **Connectivity check** — `GET /api/health/db` tests the CRM connection (server,
  DB, driver) without a full pull.
- **What's loaded** — `GET /api/data-source` (and the cockpit banner) show the
  active source, pilot scope, and any load warnings.
- **Pilot scoping** — the catalogue is ~14k items; `CRM_DIVISION` (one Division),
  `ACTIVE_ONLY` (items with recent dispatch) and `MAX_SKUS` (top-N by volume)
  keep the first run fast and focused, per the blueprint's "pilot family first".
- **Graceful degradation** — a source that errors (e.g. a not-yet-granted SP like
  `SP_SOCSummaryReport`) is caught, surfaced as a load warning, and planning
  continues on what loaded instead of crashing the cycle.
- **Self-documenting mapping** — non-confirmed columns are matched against
  candidate names; if none match, the error names the available columns and the
  exact `COLUMN_MAP` key to fix in `adapter.py`.

## The demo loop

1. **S&OP Cockpit** — pipeline status, exception mix, Sales-owner bias guardrail, RCCP feasibility.
2. **Demand Validation** — exception inbox. Open a flagged SKU to see its triangulation chart,
   adjust the number (the SOC floor rule is enforced), attach a reason code, and confirm.
3. **Lock consensus** — freezes the demand number; supply planning then consumes only the locked plan.
4. **Segmentation** — ABC-XYZ matrix, FG PTO/PTS recommendations with rationale, RM Kraljic policy.
5. **Supply & RM Plan** — net FG requirements, safety stock, RM net-buy plan, rough-cut capacity gaps.
6. **KPI Framework** — forecast quality (back-tested), service, inventory, procurement KPIs vs target.
7. **Audit Trail** — every confirmation/lock recorded with actor, timestamp and reason code.

Use **↺ Reset cycle** (sidebar) to restore the initial synthetic state at any time.

## Notes & scope

The synthetic dataset deliberately seeds every exception type (over/under-projection, signal
conflict, NPI, erratic, stale) **and every data-quality defect** (missing lead time on a critical
single-source RM, negative on-hand, stale price, unsigned BOM version) so the validation engine and
DQ gate are fully exercised. All 16 blueprint sections are now implemented end-to-end.

Forecasting and analytics use **transparent statistical methods rather than opaque ML** so every
flag (anomaly z-scores, risk scores, supplier late-probability, champion/challenger) is explainable
and tied to a decision and an owner — consistent with the blueprint's "decisions over reports"
principle. The one deliberate simplification that remains is **single-period planning** (the cycle
plans one month); multi-period MRP buckets are the natural next extension.
