import re
import time
import json
from typing import Tuple, Dict, Any, Optional

from backend.guardrails.schemas import GuardrailVerdict
from backend.observability.logger import log_guardrail_event
from backend.observability.tracer import traceable
from backend.config import ROLE_COLLECTIONS, ROLES

# Common adversarial jailbreak & injection patterns
INJECTION_PATTERNS = [
    r"(?i)\bignore\s+(all\s+)?(previous|prior|above)\s+instructions\b",
    r"(?i)\bsystem\s+override\b",
    r"(?i)\bdisregard\s+(all\s+)?(role|access|security)?\s*(policies|rules|guidelines)\b",
    r"(?i)\bbypass\s+(security|rbac|filter|guardrail)\b",
    r"(?i)\byou\s+are\s+now\s+(in\s+)?dan\s+mode\b",
    r"(?i)\bact\s+as\s+(an?\s+)?(unrestricted|root|admin|superuser)\b",
    r"(?i)\breveal\s+(your\s+)?(system\s+prompt|secret|api[_\s-]?key)\b",
    r"(?i)\bpretend\s+you\s+have\s+no\s+(rules|restrictions|limits)\b"
]

OFF_TOPIC_ABUSE_PATTERNS = [
    r"(?i)\b(write\s+a\s+poem|tell\s+me\s+a\s+joke|who\s+won\s+the\s+world\s+cup|crypto\s+trading|lottery)\b",
    r"(?i)\b(how\s+to\s+make\s+a\s+bomb|hack\s+into|steal\s+credit\s+card|exploit\s+vulnerability)\b"
]

# API key detection
API_KEY_PATTERNS = [
    r"(?i)\b(api_key|secret_key|gemini_api_key|openai_api_key|groq_api_key|password123)\b",
    r"(?i)\bshow\s+me\s+(the\s+)?(env|\.env|environment\s+variables|api\s+keys?)\b"
]

GIBBERISH_PATTERNS = [
    r"(?i)\b(asdf|qwerty|zxcvbnm|laksjd|1234567890)\b",
]

@traceable(name="guardrails.input_check", run_type="chain")
def evaluate_input_guardrail(
    query: str,
    role: str,
    request_id: str
) -> GuardrailVerdict:
    """
    Executes pre-flight Input Guardrails against incoming query.
    Fails closed: Any failure or malformed verdict results in a blocked request.
    """
    start_time = time.time()
    clean_q = (query or "").strip()

    # 1. Validation check
    if not clean_q:
        latency = (time.time() - start_time) * 1000
        verdict = GuardrailVerdict(
            allowed=False,
            violation_type="empty_query",
            internal_reason="Empty or whitespace query provided.",
            user_message="Please enter a valid question regarding hospital operations, clinical guidelines, equipment, or billing."
        )
        log_guardrail_event(request_id, "input", role, "BLOCKED", verdict.violation_type, verdict.internal_reason, latency)
        return verdict

    if role not in ROLES:
        latency = (time.time() - start_time) * 1000
        verdict = GuardrailVerdict(
            allowed=False,
            violation_type="invalid_role",
            internal_reason=f"Role '{role}' is not in allowed ROLES registry.",
            user_message="Access denied: Unknown or unauthorized user role."
        )
        log_guardrail_event(request_id, "input", role, "BLOCKED", verdict.violation_type, verdict.internal_reason, latency)
        return verdict

    # 2. Check for API key / credential extraction attempts
    for pattern in API_KEY_PATTERNS:
        if re.search(pattern, clean_q):
            latency = (time.time() - start_time) * 1000
            verdict = GuardrailVerdict(
                allowed=False,
                violation_type="credential_leak_attempt",
                internal_reason=f"Matched credential extraction pattern: {pattern}",
                user_message="Security Alert: Inquiries regarding internal credentials, API keys, or secrets are strictly forbidden by network security policy."
            )
            log_guardrail_event(request_id, "input", role, "BLOCKED", verdict.violation_type, verdict.internal_reason, latency)
            return verdict

    # 3. Check for prompt injection / jailbreak attempts
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, clean_q):
            latency = (time.time() - start_time) * 1000
            verdict = GuardrailVerdict(
                allowed=False,
                violation_type="prompt_injection",
                internal_reason=f"Matched prompt injection pattern: {pattern}",
                user_message="Your request was blocked by the input guardrail due to detected prompt injection or policy override patterns."
            )
            log_guardrail_event(request_id, "input", role, "BLOCKED", verdict.violation_type, verdict.internal_reason, latency)
            return verdict

    # 4. Check for off-topic abuse / hazardous topics
    for pattern in OFF_TOPIC_ABUSE_PATTERNS:
        if re.search(pattern, clean_q):
            latency = (time.time() - start_time) * 1000
            verdict = GuardrailVerdict(
                allowed=False,
                violation_type="off_topic_abuse",
                internal_reason=f"Matched off-topic or hazardous abuse pattern: {pattern}",
                user_message="MediBot is restricted to MediAssist healthcare operations, clinical protocols, and hospital systems. Non-medical or unauthorized inquiries are disallowed."
            )
            log_guardrail_event(request_id, "input", role, "BLOCKED", verdict.violation_type, verdict.internal_reason, latency)
            return verdict

    # 4b. Check for nonsensical / gibberish inputs
    for pattern in GIBBERISH_PATTERNS:
        if re.search(pattern, clean_q):
            latency = (time.time() - start_time) * 1000
            verdict = GuardrailVerdict(
                allowed=False,
                violation_type="gibberish_input",
                internal_reason=f"Matched gibberish or nonsensical character sequence: {pattern}",
                user_message="Your inquiry could not be understood. Please enter a valid medical, operational, or hospital query."
            )
            log_guardrail_event(request_id, "input", role, "BLOCKED", verdict.violation_type, verdict.internal_reason, latency)
            return verdict

    # 5. Check role-targeted boundary override attempts
    # If a role explicitly tries to demand access to a domain outside their role:
    allowed_domains = ROLE_COLLECTIONS.get(role, ["general"])
    forbidden_mappings = {
        "billing": ["billing code", "claim tariff", "insurance tariff", "cpt code", "icd-10 reimbursement rate", "claim submission portal", "financial amount", "approved financial amount", "claims database", "claims rejected"],
        "clinical": ["clinical drug formulary", "pediatric dosage table", "contraindication table", "epinephrine dosage"],
        "equipment": ["ventilator calibration guide", "defibrillator maintenance manual", "autoclave error codes"]
    }

    for forbidden_domain, keywords in forbidden_mappings.items():
        if forbidden_domain not in allowed_domains:
            for kw in keywords:
                if kw in clean_q.lower():
                    latency = (time.time() - start_time) * 1000
                    verdict = GuardrailVerdict(
                        allowed=False,
                        violation_type="role_boundary_violation",
                        internal_reason=f"Role '{role}' attempted to access forbidden '{forbidden_domain}' domain via keyword '{kw}'.",
                        user_message=f"Access Restricted: As a {role}, you do not have permission to access {forbidden_domain} documents. Please consult authorized personnel."
                    )
                    log_guardrail_event(request_id, "input", role, "BLOCKED", verdict.violation_type, verdict.internal_reason, latency)
                    return verdict

    # 6. Passed all checks
    latency = (time.time() - start_time) * 1000
    verdict = GuardrailVerdict(
        allowed=True,
        violation_type=None,
        internal_reason=None,
        user_message=None
    )
    log_guardrail_event(request_id, "input", role, "ALLOWED", None, None, latency)
    return verdict
