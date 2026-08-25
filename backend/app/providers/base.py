from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.models import CallStatus


@dataclass(frozen=True)
class ProviderCallEvent:
    provider_call_id: str
    status: CallStatus
    direction: str
    from_number: str
    to_number: str


@runtime_checkable
class TelephonyProvider(Protocol):
    name: str

    def verify_signature(self, url: str, parameters: dict[str, str], signature: str) -> bool: ...
    def parse_event(self, parameters: dict[str, str]) -> ProviderCallEvent: ...
