import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

LOGS_DIR = Path("e:/Tutorials/CodeBasics/AI_Engineering/12_13Sep/Assignment-3-Evaluation_Guardrail_Pipeline/MediBotRAG_Assignment/logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)
GUARDRAIL_LOG_FILE = LOGS_DIR / "guardrails.jsonl"
REQUEST_LOG_FILE = LOGS_DIR / "requests.jsonl"

def log_guardrail_event(
    request_id: str,
    stage: str,  # "input" or "output"
    role: str,
    verdict: str,  # "ALLOWED" or "BLOCKED"
    violation_type: Optional[str],
    internal_reason: Optional[str],
    latency_ms: float,
    metadata: Optional[Dict[str, Any]] = None
):
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "stage": stage,
        "role": role,
        "verdict": verdict,
        "violation_type": violation_type,
        "internal_reason": internal_reason,
        "latency_ms": round(latency_ms, 2),
        "metadata": metadata or {}
    }
    with open(GUARDRAIL_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return event

def log_request_metrics(
    request_id: str,
    role: str,
    question: str,
    retrieval_type: str,
    total_latency_ms: float,
    retrieval_latency_ms: float,
    rerank_latency_ms: float,
    llm_latency_ms: float,
    input_guardrail_latency_ms: float,
    output_guardrail_latency_ms: float,
    tokens: Dict[str, int],  # {"prompt_tokens": x, "completion_tokens": y, "total_tokens": z}
    guardrail_status: str,  # "PASSED" or "BLOCKED"
    sources_count: int
):
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "role": role,
        "question": question[:100],  # truncated for safety
        "retrieval_type": retrieval_type,
        "guardrail_status": guardrail_status,
        "latencies": {
            "total_ms": round(total_latency_ms, 2),
            "input_guardrail_ms": round(input_guardrail_latency_ms, 2),
            "retrieval_ms": round(retrieval_latency_ms, 2),
            "rerank_ms": round(rerank_latency_ms, 2),
            "llm_ms": round(llm_latency_ms, 2),
            "output_guardrail_ms": round(output_guardrail_latency_ms, 2)
        },
        "tokens": tokens,
        "sources_retrieved": sources_count
    }
    with open(REQUEST_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record
