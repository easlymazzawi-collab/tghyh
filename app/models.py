from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from app.recipe import LoginRecipe


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class LoginPayloadMode(str, Enum):
    JSON = "json"
    FORM = "form"
    HTML_FORM = "html_form"


class DiscoverFormRequest(BaseModel):
    page_url: str

    @field_validator("page_url")
    @classmethod
    def strip_page_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("page_url is required")
        return value


class DiscoveredFormResponse(BaseModel):
    page_url: str
    action_url: str
    method: str
    username_field: str
    password_field: str
    hidden_fields: Dict[str, str] = Field(default_factory=dict)
    form_index: int = 0
    confidence: int = 0
    all_forms_count: int = 0


class TestLoginRequest(BaseModel):
    login_url: str = Field(..., description="Login API URL or HTML login page URL")
    credentials: str = Field(..., description="One user:pass per line")
    username_field: str = "username"
    password_field: str = "password"
    payload_mode: LoginPayloadMode = LoginPayloadMode.JSON
    page_url: str = ""
    form_action_url: str = ""
    max_workers: int = Field(default=3, ge=1, le=10)
    rate_limit_rps: float = Field(default=2.0, ge=0.1, le=20.0)
    extra_headers: Dict[str, str] = Field(default_factory=dict)
    hidden_fields: Dict[str, str] = Field(default_factory=dict)
    post_login_url: str = Field(
        default="",
        description="Optional URL to open after login and capture screen",
    )
    capture_screen: bool = True

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


class RecordLoginRequest(BaseModel):
    name: str = ""
    login_url: str = ""
    credentials: str = Field(default="", description="Exactly one user:pass sample line")
    curl_command: str = ""
    username_field: str = "username"
    password_field: str = "password"
    payload_mode: LoginPayloadMode = LoginPayloadMode.HTML_FORM
    page_url: str = ""
    form_action_url: str = ""
    post_login_url: str = ""
    capture_screen: bool = True
    extra_headers: Dict[str, str] = Field(default_factory=dict)
    hidden_fields: Dict[str, str] = Field(default_factory=dict)
    verify_sample: bool = True


class RecordLoginResponse(BaseModel):
    recipe: Dict[str, Any]
    sample_login_ok: bool
    message: str


class ReplayJobRequest(BaseModel):
    recipe_id: str = ""
    recipe: Optional[Dict[str, Any]] = None
    credentials: str
    max_workers: int = Field(default=3, ge=1, le=10)
    rate_limit_rps: float = Field(default=2.0, ge=0.1, le=20.0)
