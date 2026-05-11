"""Routing wrapper around two Hyperliquid SDK ``Info`` clients.

Primary provider (e.g. Chainstack) handles every call except the ones it is
known to reject; those are transparently delegated to the public fallback.
"""

from typing import Optional, Set


class RoutingInfoClient:
    def __init__(
        self,
        primary,
        fallback,
        primary_unsupported: Optional[Set[str]] = None,
    ):
        self._primary = primary
        self._fallback = fallback
        self._unsupported: Set[str] = set(primary_unsupported or ())

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if self._fallback is not None and name in self._unsupported:
            return getattr(self._fallback, name)
        return getattr(self._primary, name)
