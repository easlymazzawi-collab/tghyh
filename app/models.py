from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class LoginPayloadMode(str, Enum):
    JSON = "json"
    FORM = "form"


class TestLoginRequest(BaseModel):
    login_url: str = Field(..., description="Official login API endpoint URL")
    credentials: str = Field(..., description="One user:pass per line")
    username_field: str = "username"
    password_field: str = "password"
    payload_mode: LoginPayloadMode = LoginPayloadMode.JSON
    max_workers: int = Field(default=3, ge=1, le=10)
    rate_limit_rps: float = Field(default=2.0, ge=0.1, le=20.0)
    extra_headers: Dict[str, str] = Field(default_factory=dict)

    @field_validator("login_url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("login_url is required")
        return value


class CredentialResult(BaseModel):
    username: str
    success: bool
    status_code: Optional[int] = None
    response_time_ms: Optional[float] = None
    message: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)


class JobSummary(BaseModel):
    job_id: str
    status: JobStatus
    total: int = 0
    success_count: int = 0
    failure_count: int = 0
    login_url: str = ""
    results: List[CredentialResult] = Field(default_factory=list)
    error: Optional[str] = None
