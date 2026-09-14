"""
AI connector package for SALTY AI Call Agent.
"""

from app.ai.backend_client import AIBackendClient, ai_backend_client
from app.ai.nim_client import NIMClient, nim_client

__all__ = [
    "AIBackendClient",
    "ai_backend_client",
    "NIMClient",
    "nim_client",
]
