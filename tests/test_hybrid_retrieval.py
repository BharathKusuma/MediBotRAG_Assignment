# pyrefly: ignore [missing-import]
import pytest
from backend.ingestion.parser import DocumentParser
from backend.config import DATA_DIR, COLLECTION_ACCESS_MAPPING

def test_document_parsing_and_metadata():
    parser = DocumentParser()
    chunks = parser.parse_all_documents(DATA_DIR)

    assert len(chunks) > 0, "No chunks extracted from documents!"

    # Verify all 5 collections are represented
    collections_found = set(c.collection for c in chunks)
    expected_collections = {"general", "clinical", "nursing", "billing", "equipment"}
    assert expected_collections.issubset(collections_found), f"Missing collections in {collections_found}"

    # Verify metadata fields on all chunks
    for chunk in chunks:
        d = chunk.to_dict()
        assert "source_document" in d and d["source_document"], "Missing source_document"
        assert "collection" in d and d["collection"], "Missing collection"
        assert "access_roles" in d and isinstance(d["access_roles"], list) and len(d["access_roles"]) > 0
        assert "section_title" in d and d["section_title"], "Missing section_title"
        assert "chunk_type" in d and d["chunk_type"] in ["text", "table", "heading", "code"]
        assert "contextual_text" in d and len(d["contextual_text"]) > 0

    # Verify table chunks were preserved
    table_chunks = [c for c in chunks if c.chunk_type == "table"]
    assert len(table_chunks) > 0, "No table chunks detected!"
    assert any("|" in c.content for c in table_chunks), "Table chunks do not contain table delimiters!"

if __name__ == "__main__":
    pytest.main(["-v", __file__])
