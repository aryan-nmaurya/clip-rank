"""Analytics, learning and the money dashboard. Local-only, JSON mutations only."""
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.youtube_routes import local_access, local_mutation
from app.business import dashboard, learning, store
from app.core.runtime import run_blocking

router = APIRouter(prefix="/business", tags=["Business"], dependencies=[Depends(local_access)])


class LedgerEntry(BaseModel):
    kind: Literal["revenue", "cost"]
    category: str
    amount: float = Field(ge=0, le=10_000_000)
    note: str = Field(min_length=3, max_length=300)
    occurred_on: Optional[str] = None


class ConfigUpdate(BaseModel):
    ypp_status: Optional[str] = None
    currency: Optional[str] = None
    ai_input_price_per_million: Optional[float] = None
    ai_output_price_per_million: Optional[float] = None


class AffiliateIn(BaseModel):
    brand: str
    url: str
    categories: List[str]
    disclosure: str


def invalid(action):
    try:
        return action()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/dashboard")
async def get_dashboard():
    return await run_blocking(dashboard.build)


@router.get("/ledger")
def get_ledger():
    return {"entries": store.entries()}


@router.post("/ledger", dependencies=[Depends(local_mutation)])
def add_ledger(entry: LedgerEntry):
    return invalid(lambda: store.add_entry(entry.kind, entry.category, entry.amount, entry.note, entry.occurred_on))


@router.delete("/ledger/{entry_id}", dependencies=[Depends(local_mutation)])
def remove_ledger(entry_id: int):
    if not store.delete_entry(entry_id):
        raise HTTPException(404, "Entry not found.")
    return {"deleted": entry_id}


@router.get("/config")
def get_config():
    return store.get_config()


@router.post("/config", dependencies=[Depends(local_mutation)])
def update_config(update: ConfigUpdate):
    return invalid(lambda: store.set_config(update.model_dump(exclude_unset=True)))


@router.get("/affiliates")
def list_affiliates():
    return {"affiliates": store.affiliates()}


@router.post("/affiliates", dependencies=[Depends(local_mutation)])
def create_affiliate(payload: AffiliateIn):
    return invalid(lambda: store.add_affiliate(payload.brand, payload.url, payload.categories, payload.disclosure))


@router.delete("/affiliates/{affiliate_id}", dependencies=[Depends(local_mutation)])
def remove_affiliate(affiliate_id: int):
    if not store.delete_affiliate(affiliate_id):
        raise HTTPException(404, "Affiliate program not found.")
    return {"deleted": affiliate_id}


@router.get("/insights")
def insights():
    return {"adjustments": learning.current_adjustments(), "rules": {
        "min_videos": learning.MIN_VIDEOS, "min_group": learning.MIN_GROUP, "max_weight": learning.MAX_WEIGHT}}


@router.post("/insights/refresh", dependencies=[Depends(local_mutation)])
async def refresh_insights():
    return await run_blocking(learning.refresh)


@router.post("/analytics/collect", dependencies=[Depends(local_mutation)])
async def collect_analytics():
    from app.studio.analytics import AnalyticsCollector
    return await run_blocking(AnalyticsCollector.collect)
