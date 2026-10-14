from typing import Optional, List
from pydantic import BaseModel, Field

class GuardrailVerdict(BaseModel):
    allowed: bool = Field(..., description="Whether the request/response passed the safety guardrail.")
    violation_type: Optional[str] = Field(None, description="Category of violation if blocked (e.g., prompt_injection, role_override, pii_leak, restricted_content).")
    internal_reason: Optional[str] = Field(None, description="Detailed diagnostic reason logged internally, NEVER shown to user.")
    user_message: Optional[str] = Field(None, description="Generic, safe response shown to the end user.")

class GuardrailLogEvent(BaseModel):
    timestamp: str
    request_id: str
    guardrail_stage: str  # "input" or "output"
    role: str
    verdict: str  # "ALLOWED" or "BLOCKED"
    violation_type: Optional[str] = None
    internal_reason: Optional[str] = None
    latency_ms: float
