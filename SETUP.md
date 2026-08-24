# Running the S&OP Planning Tool — Setup Guide

Two ways to run it:

- **Synthetic mode** — works on any machine in ~5 minutes. No database, no
  credentials, no data files. Best for a first look / demo.
- **Live mode** — runs on the real CRM + the Excel/CSV data files. Needs the CRM
  read-only credentials, the SQL Server ODBC driver, network access to the CRM,
  and the data files in the project root.

You always run **two things**: a **backend** (Python API) and a **frontend**
(web UI), each in its own terminal.

---

## 0. Prerequisites (install once)

| Need | For | Where |
|---|---|---|
| **Python 3.11+** | backend | https://www.python.org/downloads/ (tick "Add to PATH") |
| **Node.js 18+** | frontend | https://nodejs.org/ |
| **ODBC Driver 18 for SQL Server** | live mode only | https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server |

Check they're installed (in PowerShell):
```powershell
python --version
node --version
```

---

## 1. Backend (Terminal 1)

```powershell
cd <project-folder>\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Choose the data source — create the `.env` file
Copy `.env.example` to `.env` in the `backend` folder:
```powershell
Copy-Item .env.example .env
```
Then open `backend\.env` and set **`DATA_SOURCE`**:

- **For a quick demo (no DB/files):**
  ```
  DATA_SOURCE=synthetic
  ```
  That's all you need — skip to "Start the backend".

- **For live (real data):**
  ```
  DATA_SOURCE=live
  CRM_DB_PASSWORD=<the readuser password>
  ```
  Live mode also needs:
  1. You are on the **Pure Chemical network** (the backend must reach `10.1.0.146`).
  2. The **data files** are in the **project root** (next to this SETUP.md):
     `Pure_Stock_Agings*.xlsx`, `BOM_Planning_Tool*.xlsx`, `Pure_PO_Receipts*.csv`,
     `PCBusinessPlan*.xlsx`, `*Cycle Time*.xlsx`, `FG_Shelf_Life*.xlsx`.
     (They are auto-discovered — no paths to configure.)

### Start the backend
```powershell
python run.py
```
Wait for **`Application startup complete`** (live mode's first load takes ~20–40s
— it reads the CRM + the data files). Leave this terminal running.

- Check it's up: open <http://localhost:8000/api/data-source> — it shows the
  source (`synthetic` or `live-crm+files`) and any warnings.

---

## 2. Frontend (Terminal 2)

In a **second** terminal:
```powershell
cd <project-folder>\frontend
npm install
npm run dev
```
Wait for **`Local: http://localhost:5173/`**, then open
**<http://localhost:5173>** in a browser. That's the app.

> If `npm` isn't recognized, Node isn't on PATH. Either reinstall Node with
> "Add to PATH", or run it directly:
> `& "C:\Program Files\nodejs\node.exe" ".\node_modules\vite\bin\vite.js"`

---

## 3. Using it

Sidebar pages: **S&OP Cockpit · Data-Quality Gate · Demand Validation ·
Forecasting · Segmentation · Supply & RM Plan · JC Plan (multi-period) ·
Analytics & What-if · KPI Framework · Governance & RACI · Audit Trail**.

- The cockpit banner shows the data source and scope.
- **Demand Validation** → open a flagged item, adjust, attach a reason code, confirm.
- **Lock consensus** (cockpit) → supply planning then consumes the locked number.
- **Supply & RM Plan** → "⤓ Publish plan (Excel)" exports the plan.
- Confirmations / lock / audit **persist** across restarts (SQLite, `backend\sop_state.db`).
- **↺ Reset cycle** (sidebar) restores the initial state.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `python` / `npm` not recognized | Install Python/Node and tick "Add to PATH"; reopen the terminal. |
| `ModuleNotFoundError` | Activate the venv (`.\.venv\Scripts\Activate.ps1`) and re-run `pip install -r requirements.txt`. |
| Live: `Data source name … no default driver` | Install **ODBC Driver 18 for SQL Server**; ensure `CRM_DB_DRIVER=ODBC Driver 18 for SQL Server` in `.env`. |
| Live: `Login failed for user 'readuser'` | Wrong/empty `CRM_DB_PASSWORD` in `.env`. |
| Live: connection times out | Not on the Pure Chemical network / firewall blocking `10.1.0.146:1433`. |
| Banner says `Sample_Data` / low coverage | Old `STOCK_XLSX` override in `.env`; remove it so the full files auto-discover. |
| Want to just see it work | Set `DATA_SOURCE=synthetic` — no DB/files needed. |

The backend alone is fully usable via its API docs at <http://localhost:8000/docs>.
