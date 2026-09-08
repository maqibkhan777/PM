"""Daily reports generation and dispatch routes."""

from typing import Optional
from fastapi import APIRouter, Query
from app.core.reports.daily_report import daily_report_generator

router = APIRouter(tags=["Reports"])


@router.get("/reports/daily")
async def get_daily_report(date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")):
    """Generate structured Daily PM Activity Report."""
    report = daily_report_generator.generate_report(target_date=date)
    return report


@router.post("/reports/daily/send")
async def send_daily_report(date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")):
    """Generate and broadcast Daily PM Activity Report to Discord."""
    res = await daily_report_generator.send_report_to_discord(target_date=date)
    return res
