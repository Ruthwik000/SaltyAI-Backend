"""
Health and readiness check endpoints for SALTY AI Call Agent.
"""

import httpx
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.models.schemas import HealthResponse, ComponentHealth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=HealthResponse)
async def get_health():
    """
    Comprehensive system health check inspecting AI backend reachability,
    Sarvam API key presence, and Exotel configuration.
    """
    components = {}
    overall_status = "healthy"

    # Check Sarvam configuration
    if settings.SARVAM_API_KEY:
        components["sarvam_api"] = ComponentHealth(
            status="healthy",
            message="Sarvam API key configured"
        )
    else:
        components["sarvam_api"] = ComponentHealth(
            status="degraded",
            message="SARVAM_API_KEY is not configured"
        )
        overall_status = "degraded"

    # Check Exotel configuration
    if settings.EXOTEL_ACCOUNT_SID:
        components["exotel"] = ComponentHealth(
            status="healthy",
            message=f"Exotel Account SID configured ({settings.EXOTEL_SUB_DOMAIN})"
        )
    else:
        components["exotel"] = ComponentHealth(
            status="degraded",
            message="EXOTEL_ACCOUNT_SID not configured"
        )
        overall_status = "degraded"

    # Check AI Backend connectivity
    ai_backend_status = "healthy"
    ai_backend_msg = f"Configured at {settings.AI_BACKEND_URL}"
    latency_ms = None

    if settings.AI_BACKEND_URL:
        try:
            start_time = datetime.now(timezone.utc)
            async with httpx.AsyncClient(timeout=2.0) as client:
                backend_base = settings.AI_BACKEND_URL.rstrip("/")
                # SALTY's local backend exposes /api/health; retain /health as
                # a compatibility fallback for deployed LangGraph services.
                resp = await client.get(f"{backend_base}/api/health")
                if resp.status_code == 404:
                    resp = await client.get(f"{backend_base}/health")
                latency_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                if resp.status_code >= 400:
                    ai_backend_status = "degraded"
                    ai_backend_msg = f"AI Backend returned HTTP {resp.status_code}"
                else:
                    ai_backend_msg = f"AI Backend reachable (HTTP {resp.status_code})"
        except Exception as e:
            ai_backend_status = "degraded"
            ai_backend_msg = f"AI Backend unreachable: {str(e)}"
            if overall_status == "healthy":
                overall_status = "degraded"

    components["ai_backend"] = ComponentHealth(
        status=ai_backend_status,
        message=ai_backend_msg,
        latency_ms=latency_ms
    )

    response_payload = HealthResponse(
        status=overall_status,
        timestamp=datetime.now(timezone.utc),
        version="1.0.0",
        components=components
    )

    status_code = status.HTTP_200_OK if overall_status in ("healthy", "degraded") else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content=response_payload.model_dump(mode="json"))


@router.get("/live")
async def liveness():
    """Simple liveness probe for orchestrators."""
    return {"status": "live", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/ready")
async def readiness():
    """Readiness probe checking server operation readiness."""
    return {"status": "ready", "timestamp": datetime.now(timezone.utc).isoformat()}
