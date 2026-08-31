"""Pydantic request models shared across the routers."""
from __future__ import annotations

from pydantic import BaseModel


class ConfirmBody(BaseModel):
    sku: str
    confirmed_qty: float
    note: str = ""


class LockBody(BaseModel):
    action: str


class ApplyBom(BaseModel):
    bom_overrides: dict = {}     # {product_name: "assembly|org|designator"}
    note: str = ""


class AdhocRun(BaseModel):
    plan_id: int | None = None


class SaveJcPlan(BaseModel):
    note: str = ""


class VookiQty(BaseModel):
    quantities: dict[str, float] = {}
    product: str | None = None


class FgMapRow(BaseModel):
    sku_code: str
    product_name: str = ""


class FgMapBulk(BaseModel):
    rows: list[FgMapRow] = []


class FgSkuRow(BaseModel):
    sku_code: str
    item_desc: str = ""


class ConfirmationBody(BaseModel):
    quantity: float
    reason_code: str | None = None
    note: str | None = None
    actor: str = "Sales"


class ActorBody(BaseModel):
    actor: str = "Demand Planner"


class WhatIfBody(BaseModel):
    demand_surge_pct: float = 0.0
    family: str | None = None
    supplier_outage: str | None = None
    capacity_loss_pct: float = 0.0
