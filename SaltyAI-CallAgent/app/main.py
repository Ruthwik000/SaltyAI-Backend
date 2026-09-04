"""
Main application entrypoint for SALTY AI Call Agent.
Configures FastAPI, structured logging, middleware, lifecycle events, and routes.
"""

import logging
import asyncio
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.api.health import router as health_router
from app.api.exotel import router as exotel_router
from app.voice.websocket import router as voice_router, prewarm_greetings
from app.conversation.manager import conversation_manager

# Configure structured logging. Keep console output and add a rotating local
# file so phone-call transcripts are easy to inspect.
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
try:
    file_handler = RotatingFileHandler(
        settings.CALL_AGENT_LOG_FILE, maxBytes=5 * 1024 * 1024,
        backupCount=3, encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logging.getLogger().addHandler(file_handler)
except OSError as exc:
    logging.getLogger("salty_call_agent").warning("Could not open call log file: %s", exc)
logger = logging.getLogger("salty_call_agent")


async def periodic_session_cleanup():
    """Background task to periodically clean up abandoned or timed-out call sessions."""
    while True:
        try:
            await asyncio.sleep(600)  # Check every 10 minutes
            conversation_manager.cleanup_stale_sessions(max_idle_seconds=1800.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error during periodic session cleanup: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle management."""
    logger.info("============================================================")
    logger.info("       SALTY AI - VOICE CALL AGENT INITIALIZING             ")
    logger.info("============================================================")
    logger.info(f"Host: {settings.HOST}:{settings.PORT}")
    logger.info(f"Test Mode: {settings.CALL_AGENT_TEST_MODE}")
    logger.info(f"AI Backend URL: {settings.AI_BACKEND_URL}")
    logger.info(f"Sarvam Base URL: {settings.SARVAM_BASE_URL} (STT: {settings.SARVAM_STT_MODEL}, TTS: {settings.SARVAM_TTS_MODEL})")
    logger.info(f"Exotel Subdomain: {settings.EXOTEL_SUB_DOMAIN}")
    logger.info(f"VAD Threshold: {settings.VAD_RMS_THRESHOLD} | Silence: {settings.VAD_SILENCE_MS}ms")
    logger.info("============================================================")

    # Launch background cleanup and pre-warm tasks
    cleanup_task = asyncio.create_task(periodic_session_cleanup())
    asyncio.create_task(prewarm_greetings())

    yield


    # Shutdown
    logger.info("Shutting down SALTY AI Call Agent...")
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("SALTY AI Call Agent shutdown complete.")


app = FastAPI(
    title="SALTY AI Call Agent",
    description="Agentic Voice Call System for fishermen using basic phones and Indian regional languages.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(health_router)
app.include_router(exotel_router)
app.include_router(voice_router)


@app.get("/", tags=["Root"])
async def root():
    """Root info endpoint providing service metadata and connection instructions."""
    return JSONResponse({
        "service": "SALTY AI Call Agent",
        "version": "1.0.0",
        "description": "Exotel AgentStream Voice Gateway for Fishermen Telephony",
        "telephony_provider": "Exotel",
        "stt_engine": f"Sarvam Saaras ({settings.SARVAM_STT_MODEL})",
        "tts_engine": f"Sarvam Bulbul ({settings.SARVAM_TTS_MODEL})",
        "ai_backend_url": settings.AI_BACKEND_URL,
        "websocket_endpoints": [
            "/ws/exotel/stream",
            "/ws/voice/stream",
        ],
        "health_check": "/health",
        "documentation": "/docs",
    })
