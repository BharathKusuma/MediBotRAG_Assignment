import os
import re
from pathlib import Path
from typing import List, Dict, Any
from backend.config import DATA_DIR, COLLECTION_ACCESS_MAPPING

def clean_text(text: str) -> str:
    """Clean extra whitespaces while preserving essential paragraph structure."""
    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

class DocumentChunk:
    def __init__(
        self,
        content: str,
        source_document: str,
        collection: str,
        access_roles: List[str],
        section_title: str,
        chunk_type: str = "text"
    ):
        self.content = content
        self.source_document = source_document
        self.collection = collection
        self.access_roles = access_roles
        self.section_title = section_title
        self.chunk_type = chunk_type

    def get_contextual_text(self) -> str:
        """Prefixed representation carrying hierarchical parent heading for rich embedding."""
        if self.section_title and self.section_title != "General":
            return f"[{self.source_document} > {self.section_title}]\n{self.content}"
        return f"[{self.source_document}]\n{self.content}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "contextual_text": self.get_contextual_text(),
            "source_document": self.source_document,
            "collection": self.collection,
            "access_roles": self.access_roles,
            "section_title": self.section_title,
            "chunk_type": self.chunk_type
        }

class DocumentParser:
    def __init__(self, max_chunk_size: int = 600, chunk_overlap: int = 100):
        self.max_chunk_size = max_chunk_size
        self.chunk_overlap = chunk_overlap

    def parse_markdown(self, file_path: Path, collection: str) -> List[DocumentChunk]:
        """Parses markdown preserving headings, code blocks, tables, and paragraphs."""
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        filename = file_path.name
        access_roles = COLLECTION_ACCESS_MAPPING.get(collection, ["admin"])
        chunks: List[DocumentChunk] = []

        current_h1 = "General"
        current_h2 = ""
        current_section = "General"
        current_buffer: List[str] = []
        in_code_block = False
        in_table = False
        current_chunk_type = "text"

        def flush_buffer():
            nonlocal current_buffer, current_chunk_type
            if not current_buffer:
                return
            block_text = "".join(current_buffer).strip()
            if not block_text:
                current_buffer = []
                return

            # Apply hierarchical sub-chunking if block exceeds max_chunk_size
            sub_chunks = self._split_text_with_overlap(block_text, self.max_chunk_size, self.chunk_overlap)
            for sub in sub_chunks:
                chunks.append(DocumentChunk(
                    content=sub,
                    source_document=filename,
                    collection=collection,
                    access_roles=access_roles,
                    section_title=current_section,
                    chunk_type=current_chunk_type
                ))
            current_buffer = []
            current_chunk_type = "text"

        for line in lines:
            # Check headings
            h1_match = re.match(r'^#\s+(.+)$', line)
            h2_match = re.match(r'^##\s+(.+)$', line)
            h3_match = re.match(r'^###\s+(.+)$', line)

            if h1_match:
                flush_buffer()
                current_h1 = h1_match.group(1).strip()
                current_h2 = ""
                current_section = current_h1
                chunks.append(DocumentChunk(
                    content=current_h1,
                    source_document=filename,
                    collection=collection,
                    access_roles=access_roles,
                    section_title=current_section,
                    chunk_type="heading"
                ))
                continue
            elif h2_match:
                flush_buffer()
                current_h2 = h2_match.group(1).strip()
                current_section = f"{current_h1} > {current_h2}"
                chunks.append(DocumentChunk(
                    content=current_h2,
                    source_document=filename,
                    collection=collection,
                    access_roles=access_roles,
                    section_title=current_section,
                    chunk_type="heading"
                ))
                continue
            elif h3_match:
                flush_buffer()
                h3 = h3_match.group(1).strip()
                current_section = f"{current_h1} > {current_h2} > {h3}" if current_h2 else f"{current_h1} > {h3}"
                continue

            # Code block detection
            if line.startswith("```"):
                if in_code_block:
                    current_buffer.append(line)
                    current_chunk_type = "code"
                    flush_buffer()
                    in_code_block = False
                else:
                    flush_buffer()
                    in_code_block = True
                    current_buffer.append(line)
                continue

            if in_code_block:
                current_buffer.append(line)
                continue

            # Markdown Table detection
            if line.strip().startswith("|") and "|" in line.strip()[1:]:
                in_table = True
                current_chunk_type = "table"
                current_buffer.append(line)
                continue
            elif in_table:
                # Table ended
                in_table = False
                flush_buffer()

            # Normal text lines
            if line.strip() == "":
                flush_buffer()
            else:
                current_buffer.append(line)

        flush_buffer()
        return chunks

    def parse_pdf(self, file_path: Path, collection: str) -> List[DocumentChunk]:
        """Parses PDF preserving tables and headings with pdfplumber and hierarchical fallback."""
        import pdfplumber

        filename = file_path.name
        access_roles = COLLECTION_ACCESS_MAPPING.get(collection, ["admin"])
        chunks: List[DocumentChunk] = []

        current_heading = "General"

        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    # 1. Extract and process tables separately if present
                    tables = page.extract_tables()
                    for table in tables:
                        if table and len(table) > 1:
                            # Format table into clean Markdown representation
                            headers = [str(col).replace('\n', ' ').strip() if col else '' for col in table[0]]
                            header_row = "| " + " | ".join(headers) + " |"
                            sep_row = "| " + " | ".join(["---"] * len(headers)) + " |"
                            data_rows = []
                            for row in table[1:]:
                                row_cells = [str(cell).replace('\n', ' ').strip() if cell else '' for cell in row]
                                data_rows.append("| " + " | ".join(row_cells) + " |")
                            table_md = "\n".join([header_row, sep_row] + data_rows)
                            
                            chunks.append(DocumentChunk(
                                content=table_md,
                                source_document=filename,
                                collection=collection,
                                access_roles=access_roles,
                                section_title=f"{current_heading} (Page {page_num} Table)",
                                chunk_type="table"
                            ))

                    # 2. Extract textual content
                    text = page.extract_text() or ""
                    lines = text.split("\n")
                    buffer: List[str] = []

                    for line in lines:
                        sline = line.strip()
                        if not sline:
                            if buffer:
                                block = " ".join(buffer).strip()
                                for sub in self._split_text_with_overlap(block, self.max_chunk_size, self.chunk_overlap):
                                    chunks.append(DocumentChunk(
                                        content=sub,
                                        source_document=filename,
                                        collection=collection,
                                        access_roles=access_roles,
                                        section_title=current_heading,
                                        chunk_type="text"
                                    ))
                                buffer = []
                            continue

                        # Detect potential headings (Numbered like "1.2 Protocol" or ALL CAPS or Title Case headers)
                        is_heading = (
                            re.match(r'^(?:[0-9]+(?:\.[0-9]+)*|[A-Z][0-9]*\.)\s+[A-Z]', sline)
                            or (len(sline) < 60 and sline.isupper() and len(sline) > 3)
                            or (len(sline) < 50 and sline.endswith(":") and not sline.startswith("Note"))
                        )

                        if is_heading:
                            if buffer:
                                block = " ".join(buffer).strip()
                                for sub in self._split_text_with_overlap(block, self.max_chunk_size, self.chunk_overlap):
                                    chunks.append(DocumentChunk(
                                        content=sub,
                                        source_document=filename,
                                        collection=collection,
                                        access_roles=access_roles,
                                        section_title=current_heading,
                                        chunk_type="text"
                                    ))
                                buffer = []
                            current_heading = sline
                            chunks.append(DocumentChunk(
                                content=sline,
                                source_document=filename,
                                collection=collection,
                                access_roles=access_roles,
                                section_title=current_heading,
                                chunk_type="heading"
                            ))
                        else:
                            buffer.append(sline)

                    if buffer:
                        block = " ".join(buffer).strip()
                        for sub in self._split_text_with_overlap(block, self.max_chunk_size, self.chunk_overlap):
                            chunks.append(DocumentChunk(
                                content=sub,
                                source_document=filename,
                                collection=collection,
                                access_roles=access_roles,
                                section_title=current_heading,
                                chunk_type="text"
                            ))

        except Exception as e:
            # Fallback to PyPDF if needed
            print(f"pdfplumber error on {file_path}: {e}, falling back to PyPDF...")
            import pypdf
            reader = pypdf.PdfReader(str(file_path))
            for page_num, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                for sub in self._split_text_with_overlap(text, self.max_chunk_size, self.chunk_overlap):
                    chunks.append(DocumentChunk(
                        content=sub,
                        source_document=filename,
                        collection=collection,
                        access_roles=access_roles,
                        section_title=f"Page {page_num}",
                        chunk_type="text"
                    ))

        return chunks

    def _split_text_with_overlap(self, text: str, max_size: int, overlap: int) -> List[str]:
        """Splits long text into manageable chunks respecting sentence/word boundaries."""
        if len(text) <= max_size:
            return [text]

        # Split along sentences or punctuation
        sentences = re.split(r'(?<=[.!?\n]) +', text)
        result_chunks = []
        current_chunk = []
        current_len = 0

        for sentence in sentences:
            sentence_len = len(sentence)
            if current_len + sentence_len > max_size and current_chunk:
                result_chunks.append(" ".join(current_chunk).strip())
                # Keep overlap
                overlap_buffer = []
                overlap_len = 0
                for s in reversed(current_chunk):
                    if overlap_len + len(s) < overlap:
                        overlap_buffer.insert(0, s)
                        overlap_len += len(s)
                    else:
                        break
                current_chunk = overlap_buffer
                current_len = overlap_len

            current_chunk.append(sentence)
            current_len += sentence_len

        if current_chunk:
            result_chunks.append(" ".join(current_chunk).strip())

        return result_chunks

    def parse_all_documents(self, data_dir: Path = DATA_DIR) -> List[DocumentChunk]:
        """Iterates over all collection directories and parses all files."""
        all_chunks: List[DocumentChunk] = []
        collections = ["general", "clinical", "nursing", "billing", "equipment"]

        for coll in collections:
            coll_path = data_dir / coll
            if not coll_path.exists():
                continue
            for file_path in coll_path.glob("*"):
                if file_path.is_file():
                    if file_path.suffix.lower() == ".pdf":
                        print(f"Parsing PDF: {coll}/{file_path.name}")
                        chunks = self.parse_pdf(file_path, coll)
                        all_chunks.extend(chunks)
                    elif file_path.suffix.lower() in [".md", ".txt"]:
                        print(f"Parsing Markdown: {coll}/{file_path.name}")
                        chunks = self.parse_markdown(file_path, coll)
                        all_chunks.extend(chunks)

        return all_chunks

if __name__ == "__main__":
    parser = DocumentParser()
    chunks = parser.parse_all_documents()
    print(f"Total extracted chunks: {len(chunks)}")
    if chunks:
        print("Sample chunk:", chunks[0].to_dict())
