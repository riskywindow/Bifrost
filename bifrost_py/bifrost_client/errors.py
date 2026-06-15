"""Python BIFROST client error taxonomy."""

from __future__ import annotations


class BifrostClientError(Exception):
    """Base class for deterministic BIFROST client failures."""


class BifrostConnectionError(BifrostClientError):
    """The client could not connect to or communicate with the daemon."""


class BifrostTimeoutError(BifrostConnectionError):
    """The daemon operation exceeded the configured timeout."""


class BifrostClosedError(BifrostClientError):
    """The client was used after it was closed."""


class BifrostProtocolError(BifrostClientError):
    """The daemon sent or received an invalid transport frame."""


class BifrostValidationError(BifrostClientError):
    """A descriptor, payload, or returned object failed validation."""


class BifrostNotFoundError(BifrostClientError):
    """The requested object is not committed and servable."""


class BifrostServerError(BifrostClientError):
    """The daemon returned an explicit error frame or failed operation."""
