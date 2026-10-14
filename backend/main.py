import os
import sys
from typing import Optional, List, Dict, Any

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

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

from backend.config import (
    DEMO_USERS,
    ROLES,
    ROLE_COLLECTIONS,
    COLLECTION_ACCESS_MAPPING,
    DB_PATH,
    QDRANT_STORAGE_PATH
)
from backend.rag.hybrid_retriever import get_hybrid_retriever
from backend.rag.sql_rag import sql_rag_chain, is_analytical_question

app = FastAPI(
    title="MediBot API",
    description="Advanced Medical RAG & SQL RAG with RBAC for MediAssist Health Network",
    version="1.0.0"
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- Models -----------------
class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    username: str
    name: str
    role: str
    department: str
    accessible_collections: List[str]

class ChatRequest(BaseModel):
    question: str
    role: Optional[str] = None
    force_sql: Optional[bool] = False

class SourceCitation(BaseModel):
    source_document: str
    section_title: str
    collection: str
    rerank_score: Optional[float] = None

class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceCitation]
    retrieval_type: str  # "hybrid_rag" or "sql_rag"
    role: str
    blocked_by_rbac: bool
    sql_query: Optional[str] = None
    data: Optional[List[Dict[str, Any]]] = None

# ----------------- Endpoints -----------------

@app.get("/health")
def health_check():
    db_exists = DB_PATH.exists()
    qdrant_exists = QDRANT_STORAGE_PATH.exists()
    return {
        "status": "healthy",
        "service": "MediBot RAG Engine",
        "database_connected": db_exists,
        "qdrant_storage_ready": qdrant_exists
    }

@app.get("/demo-users")
def get_demo_users():
    """Returns available demo credentials for easy testing across roles."""
    return [
        {
            "username": uname,
            "password": uinfo["password"],
            "role": uinfo["role"],
            "name": uinfo["name"],
            "department": uinfo["department"],
            "accessible_collections": ROLE_COLLECTIONS.get(uinfo["role"], [])
        }
        for uname, uinfo in DEMO_USERS.items()
    ]

@app.post("/login", response_model=LoginResponse)
def login(request: LoginRequest):
    user_info = DEMO_USERS.get(request.username)
    if not user_info or user_info["password"] != request.password:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    role = user_info["role"]
    # Token includes role representation
    token = f"medibot-session-{request.username}-{role}"

    return LoginResponse(
        token=token,
        username=request.username,
        name=user_info["name"],
        role=role,
        department=user_info["department"],
        accessible_collections=ROLE_COLLECTIONS.get(role, [])
    )

@app.get("/collections/{role}")
@app.get("/api/collections/{role}")
def get_collections_by_role(role: str):
    if role not in ROLES:
        raise HTTPException(status_code=404, detail=f"Role '{role}' not found.")
    return {
        "role": role,
        "accessible_collections": ROLE_COLLECTIONS.get(role, [])
    }

class APIKeyRequest(BaseModel):
    provider: str
    api_key: str

@app.get("/settings/llm-status")
@app.get("/api/settings/llm-status")
def get_llm_status():
    from backend.rag.llm_service import get_llm_service
    llm = get_llm_service()
    return llm.get_status()

@app.post("/settings/api-key")
@app.post("/api/settings/api-key")
def update_api_key(req: APIKeyRequest):
    from backend.rag.llm_service import get_llm_service
    if req.provider.lower() not in ["gemini", "groq", "openai"]:
        raise HTTPException(status_code=400, detail="Provider must be 'gemini', 'groq', or 'openai'.")
    llm = get_llm_service()
    status = llm.set_api_key(req.provider, req.api_key)
    return {
        "success": True,
        "message": f"API key for '{req.provider}' updated and activated.",
        "status": status
    }

import uuid
import time
from backend.guardrails import evaluate_input_guardrail, evaluate_output_guardrail
from backend.observability import log_request_metrics, traceable

@app.post("/chat", response_model=ChatResponse)
@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest, authorization: Optional[str] = Header(None)):
    request_id = str(uuid.uuid4())
    req_start_time = time.time()
    role = request.role
    
    # Extract role from token if header provided
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")
        for u, val in DEMO_USERS.items():
            if f"medibot-session-{u}-{val['role']}" == token:
                role = val["role"]
                break

    if not role or role not in ROLES:
        raise HTTPException(
            status_code=403,
            detail="Unauthorized. A valid staff role (doctor, nurse, billing_executive, technician, admin) is required."
        )

    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    # 1. Pre-flight Input Guardrails
    in_guard_start = time.time()
    input_verdict = evaluate_input_guardrail(query=question, role=role, request_id=request_id)
    in_guard_latency = (time.time() - in_guard_start) * 1000

    if not input_verdict.allowed:
        total_lat = (time.time() - req_start_time) * 1000
        log_request_metrics(
            request_id=request_id,
            role=role,
            question=question,
            retrieval_type="guardrail_blocked",
            total_latency_ms=total_lat,
            retrieval_latency_ms=0.0,
            rerank_latency_ms=0.0,
            llm_latency_ms=0.0,
            input_guardrail_latency_ms=in_guard_latency,
            output_guardrail_latency_ms=0.0,
            tokens={"prompt_tokens": len(question.split()), "completion_tokens": 0, "total_tokens": len(question.split())},
            guardrail_status="BLOCKED",
            sources_count=0
        )
        return ChatResponse(
            answer=input_verdict.user_message or "Request blocked by safety policy.",
            sources=[],
            retrieval_type="guardrail_blocked",
            role=role,
            blocked_by_rbac=True,
            sql_query=None,
            data=None
        )

    # 2. Routing Logic: Analytical / Numbers -> SQL RAG; Knowledge -> Hybrid RAG
    if request.force_sql or is_analytical_question(question):
        print(f"Routing query '{question}' to SQL RAG for role '{role}'")
        ret_type = "sql_rag"
        ret_start = time.time()
        result = sql_rag_chain(question=question, role=role)
        target_lat = (time.time() - ret_start) * 1000
        retrieval_lat, rerank_lat, llm_lat = target_lat * 0.2, 0.0, target_lat * 0.8
    else:
        print(f"Routing query '{question}' to Hybrid RAG for role '{role}'")
        ret_type = "hybrid_rag"
        ret_start = time.time()
        retriever = get_hybrid_retriever()
        result = retriever.answer_question(query=question, role=role)
        target_lat = (time.time() - ret_start) * 1000
        retrieval_lat, rerank_lat, llm_lat = target_lat * 0.3, target_lat * 0.1, target_lat * 0.6

    # 3. Post-generation Output Guardrails
    out_guard_start = time.time()
    output_verdict = evaluate_output_guardrail(
        response_text=result.get("answer", ""),
        sources=result.get("sources", []),
        role=role,
        request_id=request_id
    )
    out_guard_latency = (time.time() - out_guard_start) * 1000

    if not output_verdict.allowed:
        total_lat = (time.time() - req_start_time) * 1000
        log_request_metrics(
            request_id=request_id,
            role=role,
            question=question,
            retrieval_type=ret_type,
            total_latency_ms=total_lat,
            retrieval_latency_ms=retrieval_lat,
            rerank_latency_ms=rerank_lat,
            llm_latency_ms=llm_lat,
            input_guardrail_latency_ms=in_guard_latency,
            output_guardrail_latency_ms=out_guard_latency,
            tokens={"prompt_tokens": len(question.split()), "completion_tokens": 15, "total_tokens": len(question.split()) + 15},
            guardrail_status="BLOCKED",
            sources_count=len(result.get("sources", []))
        )
        return ChatResponse(
            answer=output_verdict.user_message or "Response withheld due to output compliance policy.",
            sources=[],
            retrieval_type=ret_type,
            role=role,
            blocked_by_rbac=True,
            sql_query=None,
            data=None
        )

    # 4. Successful pass
    total_lat = (time.time() - req_start_time) * 1000
    prompt_tokens = len(question.split()) * 2
    completion_tokens = len(result["answer"].split())
    log_request_metrics(
        request_id=request_id,
        role=role,
        question=question,
        retrieval_type=ret_type,
        total_latency_ms=total_lat,
        retrieval_latency_ms=retrieval_lat,
        rerank_latency_ms=rerank_lat,
        llm_latency_ms=llm_lat,
        input_guardrail_latency_ms=in_guard_latency,
        output_guardrail_latency_ms=out_guard_latency,
        tokens={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens},
        guardrail_status="PASSED",
        sources_count=len(result.get("sources", []))
    )

    return ChatResponse(
        answer=result["answer"],
        sources=[
            SourceCitation(
                source_document=s["source_document"],
                section_title=s["section_title"],
                collection=s["collection"],
                rerank_score=s.get("rerank_score")
            ) for s in result.get("sources", [])
        ],
        retrieval_type=result.get("retrieval_type", ret_type),
        role=result.get("role", role),
        blocked_by_rbac=result.get("blocked_by_rbac", False),
        sql_query=result.get("sql_query"),
        data=result.get("data")
    )

# Mount frontend static directory
static_dir = Path(__file__).resolve().parent / "static"
frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "out"

@app.get("/")
def serve_index():
    if static_dir.exists() and (static_dir / "index.html").exists():
        return FileResponse(
            str(static_dir / "index.html"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )
    elif frontend_dist.exists() and (frontend_dist / "index.html").exists():
        return FileResponse(
            str(frontend_dist / "index.html"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )
    return {"message": "MediBot API Running. Frontend not found."}

if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
elif frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)

