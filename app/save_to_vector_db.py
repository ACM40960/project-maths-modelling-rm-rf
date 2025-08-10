from imports import *

# --- helpers ---------------------------------------------------------------

def _stable_id(doc: Document) -> str:
    """
    Create a deterministic ID for a Document based on content + metadata.

    The ID is an MD5 hash of: "{page_content}||{sorted_json(metadata)}".

    Args:
        doc: LangChain Document.

    Returns:
        str: Hex digest suitable for use as a FAISS vector ID.
    """
    meta_json = json.dumps(doc.metadata or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.md5((doc.page_content + "||" + meta_json).encode("utf-8")).hexdigest()

def _norm_ext(ext: str) -> str:
    ext = (ext or "").lower().lstrip(".")
    return ext

def _guess_ext(doc: Document) -> str:
    # 1) try metadata.file_ext
    ext = _norm_ext(str(doc.metadata.get("file_ext", "")))
    # some pipelines put "code" there; treat as unknown
    if ext in {"", "code"}:
        # 2) try source path
        src = str(doc.metadata.get("source", ""))
        if src:
            ext = _norm_ext(Path(src).suffix)
    # 3) fall back to name
    if not ext and doc.metadata.get("name"):
        ext = _norm_ext(Path(str(doc.metadata["name"])).suffix)
    return ext

TEXT_EXTS = {"md", "rst", "txt"}                       # pure prose
CONFIG_EXTS = {"yml", "yaml", "toml", "ini", "cfg"}    # config (we'll treat as CODE for retrieval)
NB_EXTS = {"ipynb"}                                    # notebooks
CODE_EXTS = {"py", "js", "ts", "java", "go", "cpp", "c", "cs", "rb", "php"}

def _is_text(doc: Document) -> bool:
    """
    Heuristically classify a Document as TEXT (True) or CODE (False).

    Uses `metadata.type`, filename/extension, and special-case names (e.g., README,
    LICENSE, requirements.txt). See implementation for full rules.

    In short, Decide TEXT vs CODE using both 'type' and real extension.
    """

    t = str(doc.metadata.get("type", "")).lower()
    ext = _guess_ext(doc)

    # type-first rules
    if "docstring" in t or t in {"readme", "markdown", "inline_comment", "markdown_cell", "module_docstring"}:
        return True
    if t in {"functiondef", "asyncfunctiondef", "classdef", "code", "ipynb_cell"}:
        return False

    # extension fallbacks
    if ext in TEXT_EXTS:
        return True
    if ext in CODE_EXTS | CONFIG_EXTS | NB_EXTS:
        return False

    # special names
    name = str(doc.metadata.get("name", "")).lower()
    if name in {"readme.md", "readme.rst", "readme.txt", "license", "license.txt"}:
        return True
    if name == "requirements.txt":  # treat as TEXT so overview/installation finds it
        return True

    # default to CODE (safer for repos)
    return False

# --- main saver ------------------------------------------------------------

def save_to_faiss_split_by_ext(
    chunks: List[Document],
    base_dir: str = "docs_index",
    model: str = "text-embedding-3-small",  # or "text-embedding-3-large"
    min_chars: int = 30,
    max_chars: int = 10000,
):

    """
    Build two FAISS indexes—one for TEXT and one for CODE—then save them to disk.

    This function:
      1) Filters out tiny/huge chunks using `min_chars`/`max_chars`.
      2) Splits the remaining Documents into TEXT vs CODE via `_is_text(...)`
         (uses metadata/type + extension heuristics).
      3) Embeds each split with OpenAI embeddings.
      4) Creates two FAISS vector stores (text and code) with stable, deterministic IDs.
      5) Persists them under:
            {base_dir}/text_index
            {base_dir}/code_index
         using `FAISS.save_local(...)`.

    Args:
        chunks: Documents to index (e.g., from a repo parsing pipeline).
        base_dir: Directory where the two FAISS indexes will be saved.
        model: OpenAI embedding model name (e.g., "text-embedding-3-small" or "-3-large").
        min_chars: Minimum character length to keep a chunk.
        max_chars: Maximum character length to keep a chunk.

    Returns:
        dict: Summary containing:
            - "text_count": number of TEXT docs indexed
            - "code_count": number of CODE docs indexed
            - "model": embedding model used

    Side Effects:
        - Creates (or overwrites) folders:
            {base_dir}/text_index
            {base_dir}/code_index
        - Writes FAISS index files to those directories.
        - Prints a one-line summary to stdout.

    Requirements:
        - `OPENAI_API_KEY` must be set in the environment for embeddings.
        - `langchain_openai.OpenAIEmbeddings` and `langchain_community.vectorstores.FAISS`.

    Notes:
        - IDs are stable via `_stable_id(doc)`, so the same content+metadata yields
          the same vector ID across runs (useful for dedup / reproducibility).
        - TEXT includes README/docstrings/markdown/etc.; CODE includes code/config/notebooks.
        - If a split is empty, `FAISS.from_documents([] , ...)` may raise—consider guarding
          if you expect repos with only TEXT or only CODE.

    Example:
        >>> result = save_to_faiss_split_by_ext(docs, base_dir="my_index")
        >>> result
        {'text_count': 128, 'code_count': 342, 'model': 'text-embedding-3-small'}

        # Later, to load:
        >>> from langchain_openai import OpenAIEmbeddings
        >>> from langchain_community.vectorstores import FAISS
        >>> embedder = OpenAIEmbeddings(model="text-embedding-3-small")
        >>> text_vs = FAISS.load_local("my_index/text_index", embedder, allow_dangerous_deserialization=True)
        >>> code_vs = FAISS.load_local("my_index/code_index", embedder, allow_dangerous_deserialization=True)
    """

    # filter once
    docs = [d for d in chunks if d and d.page_content and min_chars <= len(d.page_content) <= max_chars]

    # split
    text_docs = [d for d in docs if _is_text(d)]
    code_docs = [d for d in docs if not _is_text(d)]

    # embedder
    embedder = OpenAIEmbeddings(model=model)

    # TEXT index
    text_path = Path(base_dir) / "text_index"
    text_path.mkdir(parents=True, exist_ok=True)
    text_vs = FAISS.from_documents(text_docs, embedder, ids=[_stable_id(d) for d in text_docs])
    text_vs.save_local(str(text_path))

    # CODE index
    code_path = Path(base_dir) / "code_index"
    code_path.mkdir(parents=True, exist_ok=True)
    code_vs = FAISS.from_documents(code_docs, embedder, ids=[_stable_id(d) for d in code_docs])
    code_vs.save_local(str(code_path))

    print(f"✅ saved:\n  text -> {text_path}  ({len(text_docs)} docs)\n  code -> {code_path}  ({len(code_docs)} docs)")
    return {"text_count": len(text_docs), "code_count": len(code_docs), "model": model}
