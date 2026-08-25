"""Telephony provider adapters.

Resolution goes through this registry so TelephonyProvider is a constraint the
code actually depends on rather than a shape nothing is checked against: adding
a carrier means registering a factory here, and a type checker verifies the
adapter satisfies the protocol at the point of registration.
"""

from __future__ import annotations

from typing import Callable

from app.providers.base import ProviderCallEvent, TelephonyProvider
from app.providers.twilio import TwilioProvider


_FACTORIES: dict[str, Callable[[str], TelephonyProvider]] = {
    TwilioProvider.name: TwilioProvider,
}


def get_provider(name: str, auth_token: str) -> TelephonyProvider | None:
    """Build the adapter registered under ``name``, or None if unknown."""
    factory = _FACTORIES.get(name)
    return None if factory is None else factory(auth_token)


def supported_providers() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))


__all__ = [
    "ProviderCallEvent",
    "TelephonyProvider",
    "TwilioProvider",
    "get_provider",
    "supported_providers",
]
