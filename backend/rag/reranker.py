from typing import List, Dict, Any, Tuple
from sentence_transformers import CrossEncoder
from backend.config import RERANKER_MODEL, TOP_K_RERANKED

class CrossEncoderReranker:
    def __init__(self, model_name: str = RERANKER_MODEL):
        print(f"Loading Cross-Encoder Reranker model: {model_name}...")
        self.model = CrossEncoder(model_name)

    def rerank(
        self,
        query: str,
        candidate_chunks: List[Dict[str, Any]],
        top_k: int = TOP_K_RERANKED
    ) -> List[Dict[str, Any]]:
        """
        Reranks a list of candidate chunks against the query using cross-encoder joint scoring.
        Returns the top_k candidate chunks sorted by relevance score.
        """
        if not candidate_chunks:
            return []

        # Prepare pairs for joint cross-encoder scoring
        pairs = []
        for chunk in candidate_chunks:
            # Use contextual text (containing heading) for richest relevance assessment
            text = chunk.get("contextual_text") or chunk.get("content") or ""
            pairs.append([query, text])

        # Compute cross-encoder relevance scores
        scores = self.model.predict(pairs)

        # Attach scores to candidates
        scored_candidates: List[Tuple[float, Dict[str, Any]]] = []
        for score, chunk in zip(scores, candidate_chunks):
            chunk_with_score = dict(chunk)
            chunk_with_score["rerank_score"] = float(score)
            scored_candidates.append((float(score), chunk_with_score))

        # Sort descending by score
        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        try:
            print(f"\n--- Reranker Analysis for Query: '{query}' ---")
            for rank, (score, chunk) in enumerate(scored_candidates, start=1):
                src = chunk.get("source_document", "doc")
                sec = chunk.get("section_title", "section")
                try:
                    print(f"  Rank #{rank} [Score: {score:.4f}]: {src} -> {sec}")
                except UnicodeEncodeError:
                    safe_sec = str(sec).encode("ascii", "replace").decode("ascii")
                    print(f"  Rank #{rank} [Score: {score:.4f}]: {src} -> {safe_sec}")
            print("---------------------------------------------------\n")
        except Exception:
            pass

        reranked_top_k = [item[1] for item in scored_candidates[:top_k]]
        return reranked_top_k

_reranker_instance = None

def get_reranker() -> CrossEncoderReranker:
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = CrossEncoderReranker()
    return _reranker_instance
