from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictInt, field_validator

ShortText = Annotated[str, Field(min_length=1, max_length=160)]
Phone = Annotated[str, Field(pattern=r"^\+[1-9]\d{7,14}$")]


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=8, max_length=128)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: dict


class DistributorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    external_id: str
    name: str
    location: str
    categories: list[str]
    status: str


class EnquiryCreate(BaseModel):
    product: ShortText
    quantity: StrictInt = Field(gt=0, le=1_000_000)
    unit: Literal["kg", "tonnes", "units"]
    destination: ShortText
    distributor_id: str | None = Field(default=None, max_length=32)

    @field_validator("product", "destination")
    @classmethod
    def strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class EnquiryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    display_id: str
    buyer_id: str
    product: str
    quantity: int
    unit: str
    destination: str
    status: str
    distributor_id: str | None
    created_at: datetime


class VoiceAgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    provider: str
    voice: str
    is_active: bool
    created_at: datetime


class SimulatedCallRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=32)
    direction: Literal["inbound", "outbound"]
    from_number: Phone
    to_number: Phone
    transcript: str = Field(default="", max_length=20_000)
    outcome: str | None = Field(default=None, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=160)


class CallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    agent_id: str
    provider: str
    provider_call_id: str
    direction: str
    from_number: str
    to_number: str
    status: str
    transcript: str
    summary: str | None
    outcome: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime


class DashboardResponse(BaseModel):
    counts: dict[str, int]
    agents: list[VoiceAgentResponse]
    calls: list[CallResponse]
    enquiries: list[EnquiryResponse]
