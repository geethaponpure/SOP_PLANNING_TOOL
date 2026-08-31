"""S&OP Planning Tool — backend entry point.

Run the whole backend with:

    python main.py            # serves http://127.0.0.1:8000  (docs at /docs)

Structure:
    app/routers/    HTTP endpoints (one module per domain)
    app/api/        business logic + cached loaders the routers call
    app/engine/     pure planning algorithms
    app/integration/ data access (CRM, MySQL, Excel/CSV files, JSON stores)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db_state                 # importing `app` also loads backend/.env
from app.routers import all_routers
from app.prewarm import start_prewarm


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs only in the serving worker (not the reload-manager parent), so the
    # heavy CRM prewarm happens exactly once per boot instead of twice.
    start_prewarm()
    yield


app = FastAPI(title="S&OP Planning Tool", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
db_state.init()

for _router in all_routers:
    app.include_router(_router)


if __name__ == "__main__":
    # reload watches only the source package (app/), so writing cache/DB/data files
    # into the project no longer triggers a spurious restart mid-prewarm.
    uvicorn.run(
        "main:app", host="127.0.0.1", port=8000, reload=True,
        reload_dirs=["app"],
        reload_excludes=["*.db", "*.json", "*.pkl", "*.xlsx", "*.xls", "*.csv"],
    )
