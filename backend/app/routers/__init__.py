"""Router registry — every APIRouter mounted by the app, in one list."""
from . import (
    demand, supply, mfg_stock, msl, vooki, adhoc, aged_rm, projection,
    scheduling, procurement, admin, sync, srdms, user_master, roles,
)

all_routers = [
    demand.router,
    supply.router,
    mfg_stock.router,
    msl.router,
    vooki.router,
    adhoc.router,
    aged_rm.router,
    projection.router,
    scheduling.router,
    procurement.router,
    admin.router,
    sync.router,
    srdms.router,
    user_master.router,
    roles.router,
]
