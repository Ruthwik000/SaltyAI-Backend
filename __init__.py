"""SALTY Phase 1 data-layer clients."""

from .erddap_client import (
    DatasetNotFoundError,
    ERDDAPClient,
    ERDDAPConnectionError,
    ERDDAPError,
    MetadataValidationError,
)

__all__ = [
    "DatasetNotFoundError",
    "ERDDAPClient",
    "ERDDAPConnectionError",
    "ERDDAPError",
    "MetadataValidationError",
]
