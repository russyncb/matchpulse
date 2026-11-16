"""The only error types the rest of the app needs to know about.

Adapters translate every provider-specific failure into one of these.
"""


class ProviderError(Exception):
    """Base class for all provider problems."""


class QuotaExhausted(ProviderError):
    def __init__(self, message: str = "quota exhausted", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after  # seconds, if the provider says


class RateLimited(ProviderError):
    def __init__(self, message: str = "rate limited", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderDown(ProviderError):
    """Timeouts, connection errors, 5xx responses. Worth retrying."""


class BadResponse(ProviderError):
    """The provider answered but the data was unusable. Not worth retrying."""


class ProviderBlocked(ProviderError):
    """Raised by the gateway WITHOUT calling the provider (cooling down or out of budget)."""
