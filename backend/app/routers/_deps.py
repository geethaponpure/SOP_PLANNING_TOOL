"""Shared imports for every router — kept in one place so each router file is just
``from ._deps import *`` + its route handlers.

Re-exports: FastAPI helpers, the engine modules, the integration source aliases,
the service-layer helpers (api.common / api.core / api.live) and the Pydantic
request models. Route bodies moved out of the old ``main.py`` keep working verbatim.
"""
from __future__ import annotations

import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
from collections import Counter  # noqa: F401
from datetime import datetime  # noqa: F401

from fastapi import APIRouter, File, Form, HTTPException, UploadFile  # noqa: F401
from fastapi.responses import StreamingResponse  # noqa: F401

from .. import db_state  # noqa: F401
from ..engine import baseline as bl  # noqa: F401
from ..engine import dq as dq_engine  # noqa: F401
from ..engine import segmentation as seg  # noqa: F401
from ..engine import supply as sp  # noqa: F401
from ..engine import validation as val  # noqa: F401
from ..engine import forecasting as fc  # noqa: F401
from ..engine import analytics  # noqa: F401
from ..engine import kpis  # noqa: F401
from ..engine import governance  # noqa: F401
from ..engine.jc_plan import build_jc_plan  # noqa: F401
from ..engine.adhoc import build_adhoc_plan  # noqa: F401
from ..engine.ppv import build_ppv  # noqa: F401
from ..engine import receipt_schedule as _rsched  # noqa: F401
from ..engine.supplier_scorecard import build_supplier_scorecard  # noqa: F401

from ..integration import planning_filter as _pf  # noqa: F401
from ..integration import planning_settings as _ps  # noqa: F401
from ..integration import crm_sources as _crm  # noqa: F401
from ..integration import jc_calendar as _jc  # noqa: F401
from ..integration import mysql_db as _mysql  # noqa: F401
from ..integration import scheduling as _sched  # noqa: F401
from ..integration import rm_consumption as _rmc  # noqa: F401
from ..integration import projection_accuracy as _pacc  # noqa: F401
from ..integration import msl as _msl  # noqa: F401
from ..integration.adapter import _resolve_file, _resolve_po_files, resolve_latest_po_register  # noqa: F401
from ..integration import staging  # noqa: F401
from .. import publish as _pub  # noqa: F401

from ..api.common import *   # noqa: F401,F403
from ..api.core import *     # noqa: F401,F403
from ..api.live import *     # noqa: F401,F403
from ..schemas import *      # noqa: F401,F403

# Re-export EVERYTHING (including the underscore-prefixed service helpers), so a
# router's ``from ._deps import *`` pulls in _rm_planning, _get_data, _pf, … too.
# (A bare ``import *`` skips leading-underscore names unless __all__ lists them.)
__all__ = [n for n in dir() if not n.startswith("__")]
