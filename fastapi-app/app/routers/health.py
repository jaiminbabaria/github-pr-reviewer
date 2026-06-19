"""Liveness endpoint used by nginx / load balancers / uptime checks."""
from fastapi import APIRouter

from ..models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok", service="github-pr-reviewer")
