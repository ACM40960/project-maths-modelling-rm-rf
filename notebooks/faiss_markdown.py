import os
from pathlib import Path
from typing import List, Tuple

from langchain_core.documents import Document
from langchain.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings


def chunk_text(text: str, chunk_size: int = 3000, overlap: int = 200) -> list[str]:
    """
    Simple character-based chunking with overlap to keep context.
    """
    text = text.strip()
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    end = chunk_size
    while start < len(text):
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - overlap)
        end = start + chunk_size
    return chunks


def collect_markdown_docs(repo_path: str) -> List[Document]:
    """
    Collect README.* (from any subfolder) and all .md files into LangChain Documents.
    - Skips very short files (< 50 chars)
    - Chunks long files (> chunk_size) with small overlap so nothing is skipped
    - Avoids duplicate processing when a file matches multiple patterns
    """
    repo = Path(repo_path)
    if not repo.exists():
        raise FileNotFoundError(f"Repository path not found: {repo}")

    docs: List[Document] = []

    processed: set[Path] = set()

    def process_file(path: Path, doc_type: str):
        if path in processed:
            return
        processed.add(path)
        try:
            content = path.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError:
            return
        if len(content) <= 50:
            return
        parts = chunk_text(content, chunk_size=3000, overlap=200)
        total = len(parts)
        for i, part in enumerate(parts, start=1):
            docs.append(
                Document(
                    page_content=part,
                    metadata={
                        "source": str(path),
                        "type": doc_type,
                        "name": path.name,
                        "chunk_id": f"{i}/{total}",
                    },
                )
            )

    # 1) README files across the repo
    for pattern in ("README.md", "README.rst", "README.txt"):
        for readme_path in repo.rglob(pattern):
            process_file(readme_path, doc_type="readme")

    # 2) All Markdown files (including README.md again, but handled by processed set)
    for md_path in repo.rglob("*.md"):
        process_file(md_path, doc_type="markdown")

    return docs


def build_markdown_faiss(repo_path: str, index_dir: str = "faiss_index/markdown") -> Tuple[FAISS, List[Document]]:
    """
    Build a FAISS index from README + Markdown Documents and save locally.
    Requires OPENAI_API_KEY in env.
    Returns the FAISS vector store and the raw documents.
    """
    docs = collect_markdown_docs(repo_path)
    if not docs:
        raise ValueError("No README/Markdown documents found to index.")

    embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
    vectordb = FAISS.from_documents(docs, embedding=embeddings)

    # Ensure target folder exists
    index_path = Path(index_dir)
    index_path.mkdir(parents=True, exist_ok=True)

    vectordb.save_local(str(index_path))
    return vectordb, docs


def load_markdown_faiss(index_dir: str = "faiss_index/markdown") -> FAISS:
    """Load a saved FAISS index for markdown documents."""
    embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
    return FAISS.load_local(index_dir, embeddings, allow_dangerous_deserialization=True)


def query_markdown(index: FAISS, query: str, k: int = 5) -> List[Tuple[float, Document]]:
    """
    Query the FAISS index; returns list of (score, Document).
    """
    return index.similarity_search_with_score(query, k=k)


if __name__ == "__main__":
    # Minimal CLI usage (optional):
    import argparse

    parser = argparse.ArgumentParser(description="Build and/or query FAISS for Markdown files")
    parser.add_argument("repo_path", help="Path to cloned repository (e.g., notebooks/tmp_repo)")
    parser.add_argument("--index_dir", default="faiss_index/markdown")
    parser.add_argument("--query", default=None, help="If provided, will query the built/loaded index")
    args = parser.parse_args()

    # Build index if not present
    index_dir = args.index_dir
    if not Path(index_dir).exists():
        print(f"Building FAISS at {index_dir} from {args.repo_path} ...")
        index, _ = build_markdown_faiss(args.repo_path, index_dir=index_dir)
    else:
        print(f"Loading existing FAISS index from {index_dir} ...")
        index = load_markdown_faiss(index_dir=index_dir)

    if args.query:
        print(f"\nQuery: {args.query}")
        results = query_markdown(index, args.query, k=5)
        for rank, (score, doc) in enumerate(results, start=1):
            print(f"[{rank}] score={score:.4f} source={doc.metadata.get('source')}")
            print(doc.page_content[:400].replace("\n", " ") + ("..." if len(doc.page_content) > 400 else ""))
            print("-")
