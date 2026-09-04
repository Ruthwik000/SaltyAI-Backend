"""
API package for SALTY AI Call Agent.
"""

from app.api.health import router as health_router
from app.api.emergency import EmergencyDetector, emergency_detector

__all__ = ["health_router", "EmergencyDetector", "emergency_detector"]
