from typing import List, Dict, Any
import numpy as np
from fastembed import TextEmbedding, SparseTextEmbedding
from backend.config import DENSE_EMBEDDING_MODEL, SPARSE_EMBEDDING_MODEL

class HybridEmbedder:
    def __init__(
        self,
        dense_model_name: str = DENSE_EMBEDDING_MODEL,
        sparse_model_name: str = SPARSE_EMBEDDING_MODEL
    ):
        print(f"Loading dense embedding model: {dense_model_name}...")
        self.dense_model = TextEmbedding(model_name=dense_model_name)
        print(f"Loading sparse BM25 model: {sparse_model_name}...")
        self.sparse_model = SparseTextEmbedding(model_name=sparse_model_name)

    def embed_dense_documents(self, texts: List[str], batch_size: int = 64) -> List[List[float]]:
        """Generates dense embeddings for a batch of documents."""
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            embeddings = list(self.dense_model.embed(batch))
            all_embeddings.extend([emb.tolist() for emb in embeddings])
            print(f"  [Dense Embedding] Processed {min(i + batch_size, len(texts))}/{len(texts)} chunks", flush=True)
        return all_embeddings

    def embed_dense_query(self, query: str) -> List[float]:
        """Generates dense embedding for a single search query."""
        embeddings = list(self.dense_model.embed([query]))
        return embeddings[0].tolist()

    def embed_sparse_documents(self, texts: List[str], batch_size: int = 64) -> List[Dict[str, Any]]:
        """Generates sparse BM25 vectors for documents."""
        all_results = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            sparse_embeddings = list(self.sparse_model.embed(batch))
            for emb in sparse_embeddings:
                all_results.append({
                    "indices": emb.indices.tolist(),
                    "values": emb.values.tolist()
                })
            print(f"  [Sparse BM25] Processed {min(i + batch_size, len(texts))}/{len(texts)} chunks", flush=True)
        return all_results

    def embed_sparse_query(self, query: str) -> Dict[str, Any]:
        """Generates sparse BM25 vector for search query."""
        sparse_embeddings = list(self.sparse_model.embed([query]))
        emb = sparse_embeddings[0]
        return {
            "indices": emb.indices.tolist(),
            "values": emb.values.tolist()
        }
