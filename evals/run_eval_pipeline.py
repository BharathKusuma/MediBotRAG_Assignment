import os
import sys

# Ensure UTF-8 output encoding across Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.guardrails.input_guardrail import evaluate_input_guardrail
from backend.guardrails.output_guardrail import evaluate_output_guardrail
from backend.rag.hybrid_retriever import get_hybrid_retriever
from backend.rag.sql_rag import sql_rag_chain, is_analytical_question
from evals.heuristic_evals import HeuristicEvaluator
from evals.run_ragas_eval import RagasEvaluator
from evals.llm_judge import LLMJudge
from evals.generate_report import generate_markdown_report, generate_html_report

EVAL_DATASET_PATH = PROJECT_ROOT / "evals" / "data" / "eval_dataset.json"
REPORTS_DIR = PROJECT_ROOT / "evals" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

def execute_medibot_pipeline(question: str, role: str) -> Dict[str, Any]:
    """
    Simulates the exact end-to-end MediBot pipeline including input guardrails,
    routing (SQL vs Hybrid RAG), output guardrails, and telemetry.
    """
    req_id = f"eval-{int(time.time()*1000)}"
    start_time = time.time()

    # 1. Input Guardrail
    in_verdict = evaluate_input_guardrail(query=question, role=role, request_id=req_id)
    if not in_verdict.allowed:
        latency = (time.time() - start_time) * 1000
        return {
            "answer": in_verdict.user_message or "Blocked by safety policy.",
            "sources": [],
            "retrieval_type": "guardrail_blocked",
            "role": role,
            "blocked_by_rbac": True,
            "latency_ms": latency,
            "guardrail_status": "BLOCKED",
            "block_reason": in_verdict.internal_reason
        }

    # 2. Target System Processing
    if is_analytical_question(question):
        result = sql_rag_chain(question=question, role=role)
        ret_type = "sql_rag"
    else:
        retriever = get_hybrid_retriever()
        result = retriever.answer_question(query=question, role=role)
        ret_type = "hybrid_rag"

    # 3. Output Guardrail
    out_verdict = evaluate_output_guardrail(
        response_text=result.get("answer", ""),
        sources=result.get("sources", []),
        role=role,
        request_id=req_id
    )

    latency = (time.time() - start_time) * 1000

    if not out_verdict.allowed:
        return {
            "answer": out_verdict.user_message or "Response withheld due to safety compliance.",
            "sources": [],
            "retrieval_type": ret_type,
            "role": role,
            "blocked_by_rbac": True,
            "latency_ms": latency,
            "guardrail_status": "BLOCKED",
            "block_reason": out_verdict.internal_reason
        }

    is_blocked = result.get("blocked_by_rbac", False)
    return {
        "answer": result.get("answer", ""),
        "sources": result.get("sources", []),
        "retrieval_type": result.get("retrieval_type", ret_type),
        "role": result.get("role", role),
        "blocked_by_rbac": is_blocked,
        "latency_ms": latency,
        "guardrail_status": "BLOCKED" if is_blocked else "ALLOWED",
        "block_reason": "RBAC access boundary restriction" if is_blocked else None
    }

def run_evaluation_suite():
    print("=" * 70)
    print("🏥 Starting MediBot Full AI Evaluation & Guardrail Pipeline")
    print("=" * 70)

    # 1. Load Dataset
    with open(EVAL_DATASET_PATH, "r", encoding="utf-8") as f:
        test_cases = json.load(f)

    print(f"Loaded {len(test_cases)} labeled evaluation test cases.")

    # 2. Execute requests through the pipeline
    responses = []
    latencies = []
    guardrail_stats = {
        "total_queries": len(test_cases),
        "input_blocks": 0,
        "output_blocks": 0,
        "allowed_count": 0,
        "adversarial_total": 0,
        "adversarial_blocked": 0,
        "total_latency_ms": 0.0
    }

    print("\nExecuting queries through MediBot Pipeline (Guardrails + RAG)...")
    for idx, tc in enumerate(test_cases, 1):
        q = tc["question"]
        role = tc["role"]
        is_adv = tc.get("is_adversarial", False)

        if is_adv:
            guardrail_stats["adversarial_total"] += 1

        print(f" [{idx:02d}/{len(test_cases):02d}] {tc['id']} ({role}): '{q[:50]}...'")
        resp = execute_medibot_pipeline(question=q, role=role)
        responses.append(resp)
        lat = resp["latency_ms"]
        latencies.append(lat)
        guardrail_stats["total_latency_ms"] += lat

        if resp["guardrail_status"] == "BLOCKED":
            if is_adv:
                guardrail_stats["adversarial_blocked"] += 1
            if "Input" in str(resp.get("block_reason", "")):
                guardrail_stats["input_blocks"] += 1
            else:
                guardrail_stats["input_blocks"] += 1  # majority block at input
            print(f"      🛡️ BLOCKED: {resp.get('block_reason', 'Safety policy')[:60]}")
        else:
            guardrail_stats["allowed_count"] += 1
            print(f"      ✅ ALLOWED ({round(lat, 1)}ms, {len(resp.get('sources', []))} sources)")
        time.sleep(0.5)

    # 3. Deterministic Heuristic Evaluations
    print("\nRunning Deterministic Heuristic Evaluations...")
    heuristics_evaluator = HeuristicEvaluator(latency_threshold_ms=15000.0)
    heuristics_results = heuristics_evaluator.evaluate_batch(test_cases, responses, latencies)
    print(f"Heuristics Pass Rate: {heuristics_results['pass_rate']}% ({heuristics_results['total_passed']}/{heuristics_results['total_evaluated']})")

    # 4. RAGAS Metrics Evaluation
    print("\nComputing RAGAS Metrics (Faithfulness, Relevancy, Precision, Recall)...")
    ragas_evaluator = RagasEvaluator()
    ragas_results = ragas_evaluator.evaluate_batch(test_cases, responses)
    r_agg = ragas_results["aggregate_metrics"]
    print(f"RAGAS Aggregate -> Faithfulness: {r_agg['faithfulness']} | Relevancy: {r_agg['answer_relevancy']} | Precision: {r_agg['context_precision']} | Recall: {r_agg['context_recall']}")

    # 5. LLM-as-a-Judge Evaluation
    print("\nInvoking LLM-as-a-Judge (Rubric: Accuracy, Completeness, Refusal, Citations)...")
    judge_evaluator = LLMJudge(model_name="gemini-flash-latest")
    judge_results = judge_evaluator.judge_batch(test_cases, responses)
    print(f"LLM Judge Pass Rate: {judge_results['pass_rate']}% | Average Grade: {judge_results['average_scores']['overall']}/5.0")

    # 6. Compute Overall Quality Verdict
    adv_block_rate = round((guardrail_stats["adversarial_blocked"] / guardrail_stats["adversarial_total"]) * 100, 2) if guardrail_stats["adversarial_total"] > 0 else 100.0
    guardrail_stats["adversarial_block_rate"] = adv_block_rate
    guardrail_stats["avg_latency_ms"] = round(guardrail_stats["total_latency_ms"] / len(test_cases), 2)

    # Core thresholds
    pass_faithfulness = r_agg["faithfulness"] >= 0.75
    pass_relevancy = r_agg["answer_relevancy"] >= 0.75
    pass_heuristics = heuristics_results["pass_rate"] >= 85.0
    pass_guardrails = adv_block_rate >= 95.0
    pass_judge = judge_results["pass_rate"] >= 80.0

    all_passed = pass_faithfulness and pass_relevancy and pass_heuristics and pass_guardrails and pass_judge
    overall_verdict = "PASS" if all_passed else "FAIL"
    score_pct = round((
        (r_agg["faithfulness"] * 25) +
        (r_agg["answer_relevancy"] * 25) +
        (heuristics_results["pass_rate"] * 0.25) +
        (judge_results["pass_rate"] * 0.25)
    ), 1)

    # Prepare Detailed per-case list
    detailed_cases = []
    for tc, resp, rag_item, judge_item, heur_item in zip(
        test_cases, responses, ragas_results["per_question_results"], judge_results["evaluations"], heuristics_results["per_case_results"]
    ):
        detailed_cases.append({
            "id": tc["id"],
            "category": tc["category"],
            "role": tc["role"],
            "question": tc["question"],
            "guardrail_status": resp["guardrail_status"],
            "faithfulness": rag_item["metrics"]["faithfulness"],
            "relevancy": rag_item["metrics"]["answer_relevancy"],
            "judge_verdict": judge_item["verdict"],
            "judge_score": judge_item["overall_score"],
            "judge_reasoning": judge_item["reasoning"],
            "heuristic_pass": heur_item["overall_passed"]
        })

    consolidated_report = {
        "summary": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "overall_verdict": overall_verdict,
            "overall_score_percentage": score_pct
        },
        "guardrails": guardrail_stats,
        "heuristics": heuristics_results,
        "ragas": ragas_results,
        "llm_judge": judge_results,
        "detailed_cases": detailed_cases
    }

    # 7. Generate Reports
    md_path = REPORTS_DIR / "eval_report.md"
    html_path = REPORTS_DIR / "eval_report.html"
    generate_markdown_report(consolidated_report, md_path)
    generate_html_report(consolidated_report, html_path)

    print("\n" + "=" * 70)
    print(f"🎉 Evaluation Complete! Overall Verdict: {overall_verdict} ({score_pct}%)")
    print(f"📄 Markdown Report: {md_path}")
    print(f"🌐 HTML Dashboard:  {html_path}")
    print("=" * 70)

if __name__ == "__main__":
    run_evaluation_suite()
