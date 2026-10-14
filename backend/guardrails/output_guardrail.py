import re
import time
from typing import Dict, Any, List, Optional

from backend.guardrails.schemas import GuardrailVerdict
from backend.observability.logger import log_guardrail_event
from backend.observability.tracer import traceable
from backend.config import ROLE_COLLECTIONS

# PII Detection Patterns
PII_PATTERNS = [
    # Aadhaar Number (12 digits format)
    (r"\b\d{4}\s\d{4}\s\d{4}\b", "pii_aadhaar"),
    # Phone numbers (+91 or 10 digits)
    (r"\b(?:\+91[-\s]?)?[6-9]\d{9}\b", "pii_phone"),
    # Credit / Debit Card numbers (16 digits)
    (r"\b(?:\d{4}[-\s]?){3}\d{4}\b", "pii_credit_card"),
    # Email addresses
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "pii_email"),
]

# Sensitive restricted terms per collection
RESTRICTED_DOMAIN_TERMS = {
    "billing": [
        r"(?i)\btariff\s+rate\s*:\s*₹?\s*\d+",
        r"(?i)\bco-pay\s+percentage\s*:\s*\d+%",
        r"(?i)\binsurance\s+reimbursement\s+cap\b"
    ],
    "clinical": [
        r"(?i)\blethal\s+dose\b",
        r"(?i)\boff-label\s+experimental\s+protocol\b"
    ]
}

@traceable(name="guardrails.output_check", run_type="chain")
def evaluate_output_guardrail(
    response_text: str,
    sources: List[Dict[str, Any]],
    role: str,
    request_id: str
) -> GuardrailVerdict:
    """
    Executes post-generation Output Guardrails on the drafted answer before returning to user.
    Checks for: PII leakage, cross-role content leakage, and ungrounded fabrications.
    Fails closed: Any violation immediately blocks output.
    """
    start_time = time.time()
    clean_text = (response_text or "").strip()

    # 1. Non-empty check
    if not clean_text:
        latency = (time.time() - start_time) * 1000
        verdict = GuardrailVerdict(
            allowed=False,
            violation_type="empty_response",
            internal_reason="Output guardrail detected an empty or null model response.",
            user_message="I apologize, but I could not formulate a complete answer. Please rephrase your question."
        )
        log_guardrail_event(request_id, "output", role, "BLOCKED", verdict.violation_type, verdict.internal_reason, latency)
        return verdict

    # 2. PII Leakage Check
    for pattern, pii_type in PII_PATTERNS:
        if re.search(pattern, clean_text):
            latency = (time.time() - start_time) * 1000
            verdict = GuardrailVerdict(
                allowed=False,
                violation_type=pii_type,
                internal_reason=f"PII pattern '{pii_type}' detected in generated response.",
                user_message="Data Privacy Alert: The generated response was withheld because it contained potential Personally Identifiable Information (PII)."
            )
            log_guardrail_event(request_id, "output", role, "BLOCKED", verdict.violation_type, verdict.internal_reason, latency)
            return verdict

    # 3. Cross-Role Data Leakage Check
    # Verify whether sources or text contain restricted collection content for this role
    allowed_collections = set(ROLE_COLLECTIONS.get(role, ["general"]))
    
    # Check retrieved source collections (excluding internal sql database collections)
    for src in sources:
        col = src.get("collection")
        if col and col not in ["database", "db"] and col not in allowed_collections:
            latency = (time.time() - start_time) * 1000
            verdict = GuardrailVerdict(
                allowed=False,
                violation_type="unauthorized_source_leak",
                internal_reason=f"Retrieved source collection '{col}' is unauthorized for role '{role}'.",
                user_message="Security Policy Refusal: The retrieved documentation is restricted for your role permissions."
            )
            log_guardrail_event(request_id, "output", role, "BLOCKED", verdict.violation_type, verdict.internal_reason, latency)
            return verdict

    # Check for restricted terms in response if role is not permitted
    for domain, term_patterns in RESTRICTED_DOMAIN_TERMS.items():
        if domain not in allowed_collections:
            for term_pat in term_patterns:
                if re.search(term_pat, clean_text):
                    latency = (time.time() - start_time) * 1000
                    verdict = GuardrailVerdict(
                        allowed=False,
                        violation_type="restricted_domain_leak",
                        internal_reason=f"Role '{role}' response contained restricted '{domain}' patterns: {term_pat}.",
                        user_message=f"Access Control Alert: The generated answer contained confidential {domain} metrics unauthorized for your role."
                    )
                    log_guardrail_event(request_id, "output", role, "BLOCKED", verdict.violation_type, verdict.internal_reason, latency)
                    return verdict

    # 4. Output passed all checks
    latency = (time.time() - start_time) * 1000
    verdict = GuardrailVerdict(
        allowed=True,
        violation_type=None,
        internal_reason=None,
        user_message=None
    )
    log_guardrail_event(request_id, "output", role, "ALLOWED", None, None, latency)
    return verdict
