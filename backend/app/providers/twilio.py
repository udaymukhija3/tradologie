from __future__ import annotations

import base64
import hashlib
import hmac

from app.models import CallStatus
from app.providers.base import ProviderCallEvent

TWILIO_STATUS = {
    "queued": CallStatus.QUEUED,
    "initiated": CallStatus.QUEUED,
    "ringing": CallStatus.RINGING,
    "in-progress": CallStatus.ACTIVE,
    "completed": CallStatus.COMPLETED,
    "busy": CallStatus.FAILED,
    "failed": CallStatus.FAILED,
    "no-answer": CallStatus.FAILED,
    "canceled": CallStatus.FAILED,
}


class TwilioProvider:
    name = "twilio"

    def __init__(self, auth_token: str):
        self.auth_token = auth_token

    def signature(self, url: str, parameters: dict[str, str]) -> str:
        value = url + "".join(key + parameters[key] for key in sorted(parameters))
        digest = hmac.new(self.auth_token.encode(), value.encode(), hashlib.sha1).digest()
        return base64.b64encode(digest).decode()

    def verify_signature(self, url: str, parameters: dict[str, str], signature: str) -> bool:
        return hmac.compare_digest(self.signature(url, parameters), signature)

    def parse_event(self, parameters: dict[str, str]) -> ProviderCallEvent:
        status = TWILIO_STATUS.get(parameters.get("CallStatus", ""))
        if status is None:
            raise ValueError("Unsupported Twilio call status")
        direction = "inbound" if parameters.get("Direction", "").startswith("inbound") else "outbound"
        return ProviderCallEvent(provider_call_id=parameters["CallSid"], status=status, direction=direction, from_number=parameters["From"], to_number=parameters["To"])
