from typing import List, Dict, Any, Optional
from qdrant_client import QdrantClient
from qdrant_client.http import models

from backend.config import (
    QDRANT_STORAGE_PATH,
    COLLECTION_NAME,
    TOP_K_RETRIEVAL,
    ROLE_COLLECTIONS,
    ROLES
)
from backend.ingestion.embedder import HybridEmbedder
from backend.rag.reranker import get_reranker
from backend.rag.llm_service import get_llm_service

import tempfile
import shutil
from pathlib import Path

import re

def check_api_key_query(query: str) -> Optional[str]:
    """Detects if an API key was mistakenly submitted as a chat query."""
    clean = query.strip()
    if re.match(r'^(?:gsk_|AIzaSy|sk-|ghp_|glpat-|key-|token-|sess-)[A-Za-z0-9_\-]{16,}$', clean):
        return (
            "⚠️ **API Key Detected in Chat**: It looks like you entered an API key into the main chat window. "
            "To connect your LLM model, please paste your key into the **⚡ LLM Model Engine** section in the left sidebar and click **Save & Connect**. "
            "For security and privacy, API keys are not queried against hospital records."
        )
    if len(clean) >= 28 and re.match(r'^[A-Za-z0-9_\-]+$', clean) and ' ' not in clean and any(c.isdigit() for c in clean) and any(c.isalpha() for c in clean):
        return (
            "⚠️ **API Key / Token Detected**: It appears you entered a secret token or key into the chat. "
            "To configure your model credentials, please use the **⚡ LLM Model Engine** box in the left sidebar."
        )
    return None

def check_junk_or_gibberish(query: str) -> Optional[str]:
    """Detects random keyboard mash, non-sensical inputs, or invalid queries."""
    clean = query.strip()
    if len(clean) < 2:
        return "Please enter a valid medical, clinical, equipment, or billing question."
    
    # Pure non-alphanumeric punctuation
    if not re.search(r'[A-Za-z0-9]', clean):
        return "The query contains only special symbols. Please ask a valid clinical, equipment, or operational question."
    
    # Pure random long digit string
    if clean.isdigit() and len(clean) >= 6:
        return (
            "The query appears to be a raw numeric sequence. If searching for an ICD code, claim ID, or equipment ID, "
            "please include the identifier name (e.g. 'ICD-10 N17.9', 'CLM-2024-1000', or 'EQ-HC-3588')."
        )
    
    # Repeated characters e.g. aaaaaaa, zzzzzzz
    if re.search(r'(.)\1{4,}', clean):
        return "The query contains repeated characters and does not appear to be a valid question. Please rephrase your query."
    
    # Extract alphanumeric words
    words = [re.sub(r'[^a-zA-Z0-9]', '', w) for w in clean.split()]
    words = [w for w in words if w]
    if not words:
        return "Please enter a valid hospital, clinical, equipment, or billing question."
        
    invalid_word_count = 0
    common_mash_patterns = ['asdf', 'sdf', 'dfg', 'fgh', 'ghj', 'hjkl', 'qwer', 'wert', 'erty', 'rtyu', 'tyui', 'yuio', 'uiop', 'zxcv', 'xcvb', 'cvbn', 'vbnm']
    
    for w in words:
        wl = w.lower()
        if wl.isdigit():
            continue
        has_vowel = bool(re.search(r'[aeiouy]', wl))
        long_consonants = bool(re.search(r'[^aeiouy]{5,}', wl))
        has_mash = any(pat in wl for pat in common_mash_patterns) if len(wl) >= 4 else False
        
        if (not has_vowel and len(wl) >= 3) or long_consonants or has_mash:
            invalid_word_count += 1
            
    if (len(words) == 1 and invalid_word_count >= 1) or (invalid_word_count / len(words) >= 0.5):
        return "The input appears to be random or unrecognized text. Please ask a specific question regarding hospital protocols, clinical guidelines, equipment, or billing."
            
    return None

class HybridRetriever:
    def __init__(self):
        try:
            self.client = QdrantClient(path=str(QDRANT_STORAGE_PATH))
        except Exception as e:
            # Handle lock collision when dev server and test runner run concurrently
            try:
                temp_dir = tempfile.mkdtemp(prefix="qdrant_sync_")
                if QDRANT_STORAGE_PATH.exists():
                    for item in QDRANT_STORAGE_PATH.iterdir():
                        if item.name != ".lock":
                            target = Path(temp_dir) / item.name
                            if item.is_dir():
                                shutil.copytree(item, target)
                            else:
                                shutil.copy2(item, target)
                self.client = QdrantClient(path=temp_dir)
            except Exception as e2:
                try:
                    print(f"Notice: Qdrant lock fallback warning: {e2}. Using memory client.")
                except Exception:
                    pass
                self.client = QdrantClient(":memory:")
        self.embedder = HybridEmbedder()
        self.reranker = get_reranker()
        self.llm = get_llm_service()

    def check_collection_authorization(self, role: str, query: str) -> Optional[str]:
        """
        Detects if query explicitly targets a forbidden collection for the given role,
        providing a friendly explanation.
        """
        allowed_collections = ROLE_COLLECTIONS.get(role, ["general"])
        lower_q = query.lower()

        # Domain keywords mapping
        domain_keywords = {
            "billing": ["billing code", "claim submission", "tariff", "reimbursement", "pre-auth", "cpt code", "icd-10 code", "insurer portal", "co-pay", "billing"],
            "clinical": ["drug formulary", "dosage", "diagnostic reference", "treatment protocol", "contraindication", "hypertension guideline", "antibiotic protocol"],
            "equipment": ["equipment manual", "calibration", "defibrillator", "ventilator maintenance", "autoclave", "sterilpro", "driveflow", "sensor failure", "fault code"],
            "nursing": ["icu nursing", "infection control", "iv cannula", "catheterisation", "hand hygiene", "bed sore", "triage"]
        }

        for domain, keywords in domain_keywords.items():
            if domain not in allowed_collections:
                if any(kw in lower_q for kw in keywords):
                    allowed_str = ", ".join(f"'{c}'" for c in allowed_collections)
                    return (
                        f"As a **{role}**, you do not have access to the `{domain}` collection. "
                        f"Your access permissions are restricted to: {allowed_str}. "
                        "Security guardrails have blocked retrieval of these documents at the database layer."
                    )
        return None

    def retrieve(
        self,
        query: str,
        role: str,
        top_k: int = TOP_K_RETRIEVAL
    ) -> List[Dict[str, Any]]:
        """
        Retrieves top candidate chunks using Hybrid Search (Dense + Sparse BM25)
        with strict vector-database-layer RBAC filtering.
        """
        if role not in ROLES:
            raise ValueError(f"Unauthorized or unknown role: {role}")

        # 1. Construct RBAC Metadata Filter
        # Crucial Security Requirement: Filter applied inside Qdrant query
        rbac_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="access_roles",
                    match=models.MatchValue(value=role)
                )
            ]
        )

        # 2. Compute query embeddings
        dense_vec = self.embedder.embed_dense_query(query)
        sparse_vec = self.embedder.embed_sparse_query(query)

        candidate_chunks: List[Dict[str, Any]] = []

        try:
            # Execute Native Qdrant Hybrid Query with RRF (Reciprocal Rank Fusion)
            prefetch = [
                models.Prefetch(
                    query=dense_vec,
                    using="dense",
                    filter=rbac_filter,
                    limit=top_k * 2
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=sparse_vec["indices"],
                        values=sparse_vec["values"]
                    ),
                    using="bm25",
                    filter=rbac_filter,
                    limit=top_k * 2
                )
            ]

            response = self.client.query_points(
                collection_name=COLLECTION_NAME,
                prefetch=prefetch,
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=top_k,
                with_payload=True
            )

            for point in response.points:
                payload = point.payload or {}
                candidate_chunks.append(payload)

        except Exception as e:
            print(f"Native hybrid query error, executing fallback hybrid search: {e}")
            # Fallback hybrid retrieval: execute dense search with filter
            try:
                dense_results = self.client.search(
                    collection_name=COLLECTION_NAME,
                    query_vector=("dense", dense_vec),
                    query_filter=rbac_filter,
                    limit=top_k,
                    with_payload=True
                )
                for res in dense_results:
                    if res.payload:
                        candidate_chunks.append(res.payload)
            except Exception as e2:
                print(f"Dense search fallback error: {e2}")

        return candidate_chunks

    def answer_question(self, query: str, role: str) -> Dict[str, Any]:
        """Full Hybrid RAG pipeline: Query Validation -> RBAC Check -> Hybrid Retrieval -> Rerank -> LLM Generation."""
        # 0. API Key Detection Guardrail
        api_key_warn = check_api_key_query(query)
        if api_key_warn:
            return {
                "answer": api_key_warn,
                "sources": [],
                "retrieval_type": "hybrid_rag",
                "role": role,
                "blocked_by_rbac": False
            }

        # 1. Junk / Gibberish Validation Guardrail
        junk_warn = check_junk_or_gibberish(query)
        if junk_warn:
            return {
                "answer": junk_warn,
                "sources": [],
                "retrieval_type": "hybrid_rag",
                "role": role,
                "blocked_by_rbac": False
            }

        # 2. Explicit Domain Refusal Check
        refusal_msg = self.check_collection_authorization(role, query)
        if refusal_msg:
            return {
                "answer": refusal_msg,
                "sources": [],
                "retrieval_type": "hybrid_rag",
                "role": role,
                "blocked_by_rbac": True
            }

        # 3. Hybrid Retrieval (Top 10)
        candidates = self.retrieve(query, role, top_k=TOP_K_RETRIEVAL)

        if not candidates:
            allowed_str = ", ".join(f"'{c}'" for c in ROLE_COLLECTIONS.get(role, []))
            return {
                "answer": (
                    f"No relevant documents found within your permitted collections ({allowed_str}). "
                    "If you believe this document exists, it may belong to a restricted collection not accessible to your role."
                ),
                "sources": [],
                "retrieval_type": "hybrid_rag",
                "role": role,
                "blocked_by_rbac": False
            }

        # 4. Cross-Encoder Reranking (Narrow to Top 3)
        top_chunks = self.reranker.rerank(query, candidates, top_k=3)

        # 5. Relevance Score Guardrail: Check if even the best retrieved chunk has negligible relevance
        max_score = max((chunk.get("rerank_score") or -999 for chunk in top_chunks), default=-999)
        if max_score < -10.5:
            allowed_str = ", ".join(f"'{c}'" for c in ROLE_COLLECTIONS.get(role, []))
            return {
                "answer": (
                    f"No relevant medical, clinical, equipment, or policy documentation was found for '{query}' within your permitted collections ({allowed_str}). "
                    "Please verify that your question relates to MediAssist internal hospital procedures."
                ),
                "sources": [],
                "retrieval_type": "hybrid_rag",
                "role": role,
                "blocked_by_rbac": False
            }

        # 6. Construct Prompt Context
        context_blocks = []
        sources = []
        for chunk in top_chunks:
            src = chunk.get("source_document", "Unknown")
            sec = chunk.get("section_title", "General")
            coll = chunk.get("collection", "general")
            content = chunk.get("content", "")
            context_blocks.append(f"Document: {src} | Section: {sec}\n{content}")
            sources.append({
                "source_document": src,
                "section_title": sec,
                "collection": coll,
                "rerank_score": chunk.get("rerank_score", None)
            })

        context_str = "\n\n---\n\n".join(context_blocks)

        system_instruction = (
            "You are MediBot, an expert clinical and operational AI assistant for MediAssist Health Network. "
            "Answer the staff member's question accurately, concisely, and strictly based on the provided hospital documentation context. "
            "If the answer cannot be determined from the context, state that clearly. "
            "Always maintain high medical professionalism, cite specific document sections where helpful, and never disclose data outside the context."
        )

        prompt = f"""Context:
{context_str}

Question: {query}

Provide a clear, well-structured response citing the relevant protocols, guidelines, or procedures."""

        # 7. Generate LLM Answer
        answer = self.llm.generate(prompt=prompt, system_instruction=system_instruction)

        return {
            "answer": answer,
            "sources": sources,
            "retrieval_type": "hybrid_rag",
            "role": role,
            "blocked_by_rbac": False
        }

_retriever_instance = None

def get_hybrid_retriever() -> HybridRetriever:
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = HybridRetriever()
    return _retriever_instance
