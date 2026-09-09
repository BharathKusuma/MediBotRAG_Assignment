import os
import shutil
from pathlib import Path
from typing import List
from qdrant_client import QdrantClient
from qdrant_client.http import models

from backend.config import (
    QDRANT_STORAGE_PATH,
    COLLECTION_NAME,
    DATA_DIR
)
from backend.ingestion.parser import DocumentParser, DocumentChunk
from backend.ingestion.embedder import HybridEmbedder

def build_qdrant_index(recreate: bool = True):
    print(f"Initializing Qdrant storage at: {QDRANT_STORAGE_PATH}")
    QDRANT_STORAGE_PATH.mkdir(parents=True, exist_ok=True)

    client = QdrantClient(path=str(QDRANT_STORAGE_PATH))

    # Check collection existence
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in collections:
        if recreate:
            print(f"Recreating collection '{COLLECTION_NAME}'...")
            client.delete_collection(COLLECTION_NAME)
        else:
            print(f"Collection '{COLLECTION_NAME}' already exists.")
            return client

    # Create collection with Named Dense + Sparse BM25 vectors
    print(f"Creating collection '{COLLECTION_NAME}' with Dense (384d) + Sparse BM25 vectors...")
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            "dense": models.VectorParams(
                size=384,
                distance=models.Distance.COSINE
            )
        },
        sparse_vectors_config={
            "bm25": models.SparseVectorParams(
                index=models.SparseIndexParams(on_disk=False)
            )
        }
    )

    # 1. Parse all documents
    parser = DocumentParser(max_chunk_size=500, chunk_overlap=80)
    print("Parsing all PDF and Markdown documents in mediassist_data...")
    chunks: List[DocumentChunk] = parser.parse_all_documents(DATA_DIR)
    print(f"Extracted {len(chunks)} structured chunks.")

    if not chunks:
        print("Warning: No chunks found to index.")
        return client

    # 2. Embed chunks
    embedder = HybridEmbedder()
    texts_to_embed = [c.get_contextual_text() for c in chunks]

    print("Generating dense embeddings...")
    dense_vectors = embedder.embed_dense_documents(texts_to_embed)

    print("Generating sparse BM25 vectors...")
    sparse_vectors = embedder.embed_sparse_documents(texts_to_embed)

    # 3. Construct Qdrant points
    points = []
    for idx, (chunk, d_vec, s_vec) in enumerate(zip(chunks, dense_vectors, sparse_vectors)):
        point = models.PointStruct(
            id=idx,
            vector={
                "dense": d_vec,
                "bm25": models.SparseVector(
                    indices=s_vec["indices"],
                    values=s_vec["values"]
                )
            },
            payload=chunk.to_dict()
        )
        points.append(point)

    # 4. Upsert in batches
    batch_size = 64
    print(f"Upserting {len(points)} points into Qdrant in batches of {batch_size}...")
    for i in range(0, len(points), batch_size):
        batch = points[i:i + batch_size]
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=batch
        )
        print(f"  Indexed {min(i + batch_size, len(points))}/{len(points)} chunks")

    # 5. Create payload indexes for metadata filtering
    print("Creating payload indexes on 'access_roles' and 'collection'...")
    client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="access_roles",
        field_schema=models.PayloadSchemaType.KEYWORD
    )
    client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="collection",
        field_schema=models.PayloadSchemaType.KEYWORD
    )

    print("Qdrant index build completed successfully!")
    return client

if __name__ == "__main__":
    build_qdrant_index(recreate=True)
