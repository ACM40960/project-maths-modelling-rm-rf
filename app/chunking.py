from imports import *

def is_low_value_file(filepath):

    """
    Heuristically flag repository paths that are "low value" for indexing/docs.

    This helper filters out files and folders that are typically noise in codebases
    (e.g., build artifacts, archives, lockfiles, VCS internals). All checks are
    case-insensitive.

    A path is considered low value if **any** of the following are true:
      • Its file extension is in `low_value_exts`.
      • Its basename is in `low_value_files`.
      • Any directory segment is in `low_value_dirs`.
      • The path contains the substring "mock".

    Args:
        filepath (str | os.PathLike | pathlib.Path): Path to evaluate.

    Returns:
        bool: True if the path should be ignored (low value); False otherwise.

    Notes:
        - Only the final suffix (`Path.suffix`) is checked. For multi-suffix files
          like "archive.tar.gz", this function will match ".gz". Extend if you
          need multi-suffix handling.
        - Requires: `import os` and `from pathlib import Path`.

    Examples:
        >>> is_low_value_file("node_modules/react/index.js")
        True
        >>> is_low_value_file("src/app/main.py")
        False
        >>> is_low_value_file("README.md")
        True
    """

    low_value_exts = [
        '.css', '.min.js', '.json', '.svg', '.csv', '.xlsx', '.xls',
        '.log', '.lock', '.pyc', '.pyo', '.pyd', '.class', '.jar', '.war',
        '.o', '.obj', '.dll', '.exe', '.so', '.a', '.db', '.sqlite', '.sqlite3',
        '.bak', '.tmp', '.ico', '.icns', '.pdf', '.docx', '.pptx',
        '.7z', '.zip', '.tar', '.gz', '.rar', '.iml'
    ]

    low_value_files = [
        'readme.md', 'license', '.gitignore', '.gitattributes', 'post-update.sample',
        'fsmonitor-watchman.sample', 'pre-commit', 'pre-push', 'commit-msg',
        'tags', 'head', 'config', 'description', 'index', '.editorconfig',
        '.prettierrc', '.eslintrc', '.gitmodules', '.mailmap', '.clang-format',
        'pipfile.lock', 'yarn.lock', 'package-lock.json', '.env', '.env.example', '.npmrc',
        'update.sample'
    ]

    low_value_dirs = {
        '.git', '.vscode', '.idea', '__pycache__',
        'node_modules', 'dist', 'build', '.pytest_cache'
    }

    filepath_str = str(filepath).lower()
    parts = set(Path(filepath).parts)

    return (
        Path(filepath).suffix.lower() in low_value_exts or
        os.path.basename(filepath).lower() in low_value_files or
        any(d in parts for d in low_value_dirs) or
        'mock' in filepath_str
    )


import tempfile, shutil, os, stat
from pathlib import Path
from git import Repo

def _on_rm_error(func, path, exc_info):
    """
    Callback for `shutil.rmtree(..., onerror=...)` that fixes permission issues.

    Attempts to make the problematic `path` writable and then retries the
    originally failing operation (`func`). This is useful on platforms where
    read-only files (e.g., on Windows) can block directory removal.

    Args:
        func (Callable[[str], None]): The function that raised the error
            (typically `os.remove`, `os.rmdir`, or `shutil.rmtree` internals).
        path (str | os.PathLike): The filesystem path that could not be removed.
        exc_info (tuple): Exception triple from `sys.exc_info()`. Present for
            signature compatibility; not used.

    """
    try:
        os.chmod(path, stat.S_IWRITE)
    except Exception:
        pass
    func(path)

def clone_repo(repo_url, clone_path=None) -> str:
    """
    Clone a Git repository into a clean directory (shallow clone).

    If `clone_path` is not provided, a new temporary directory is created.
    If `clone_path` already exists, it is removed recursively (even if files
    are read-only) before cloning. The clone is shallow (`depth=1`) to reduce
    data transfer and speed up operations.

    Args:
        repo_url (str): URL or local path of the repository accepted by Git.
        clone_path (str | os.PathLike | None): Destination directory. If `None`,
            a fresh temporary directory is created (e.g., via `tempfile.mkdtemp`).

    Returns:
        str: Absolute path to the cloned working tree.

    Raises:
        git.exc.GitCommandError: If the clone operation fails.
        OSError: If the destination cannot be removed or created.

    Example:
        >>> path = clone_repo("https://github.com/user/repo.git")
        >>> Path(path).exists()
        True
    """
    # Use a fresh temp dir by default to avoid collisions
    if clone_path is None:
        clone_path = tempfile.mkdtemp(prefix="repo_")
    cp = Path(clone_path)

    if cp.exists():
        shutil.rmtree(cp, onerror=_on_rm_error)

    Repo.clone_from(repo_url, str(cp), depth=1)
    return str(cp)


def extract_all_chunks(repo_path: str, index_dir: str = "docs_index") -> list[Document]:
    """
    Walk a repository and extract “chunks” of useful text/code as LangChain `Document`s.

    The function scans `repo_path` and builds a list of `Document` objects with
    consistent metadata for downstream indexing or retrieval. It prioritizes a
    single top-level README, then collects other text files, and finally parses
    source files (Python, notebooks, and other code/text) while filtering out
    low-value paths.

    Extraction strategy:
      1) README:
         - Returns the first existing file among: `README.md|.rst|.txt`.
         - Entire file becomes one chunk when its length is in (50, 5000) chars.
      2) Other text:
         - Recursively collects `*.md`, `*.rst`, `*.txt` (excluding `README.md`).
         - Whole-file chunks when length is in (50, 5000) chars.
         - `file_ext` and `type` are based on the file’s suffix (e.g., `md`, `rst`, `txt`).
      3) Code & misc (skips anything flagged by `is_low_value_file`):
         - Python (`.py`):
             • Module docstring as a chunk (if in (50, 5000)).
             • For each top-level class/function: the full source segment (if in (50, 5000)).
             • For each such node: its docstring as a separate chunk (if in (50, 5000)).
         - Jupyter notebooks (`.ipynb`):
             • Each markdown/code cell becomes a chunk (if in (50, 5000)).
         - Other files:
             • Whole-file content as a chunk (if in (50, 5000)).

    Metadata on each `Document`:
        - source: absolute path to the file.
        - file_ext: either a suffix-derived token (e.g., `md`, `rst`, `txt`) or `"code"`.
        - type: semantic tag (e.g., `readme`, `module_docstring`, `functiondef`, `classdef`,
          `functiondef_docstring`, `markdown_cell`, `code_cell`, or the raw suffix).
        - name: filename, symbol name, or cell label.
        - lines: line range (e.g., `"1-120"`) or `"cell_{i}"` for notebooks.

    Args:
        repo_path (str): Path to the repository root to scan.
        index_dir (str, optional): Destination directory for a vector index if the
            optional FAISS block is enabled. Currently unused while that code remains
            commented out.

    Returns:
        list[Document]: A flat list of extracted chunks with metadata suitable for
        indexing or RAG pipelines.

    Notes:
        - Files failing to read/parse are skipped silently.
        - Size thresholds (50, 5000) are inclusive-exclusive checks to avoid tiny noise
          and overly large blobs.
        - Relies on: `is_low_value_file`, `ast`, `nbformat`, and `itertools.chain`.
        - `Document` is expected from LangChain (e.g., `from langchain.schema import Document`).

    Example:
        >>> docs = extract_all_chunks("/path/to/repo")
        >>> any(d.metadata.get("type") == "readme" for d in docs)
        True
    """
    chunks = []
    repo_path = Path(repo_path)

    # 1. README files
    for readme_name in ("README.md", "README.rst", "README.txt"):
        readme_path = repo_path / readme_name
        if readme_path.exists():
            content = readme_path.read_text(encoding="utf-8").strip()
            if 50 < len(content) < 5000:
                chunks.append(Document(
                    page_content=content,
                    metadata={
                        "source": str(readme_path),
                        "file_ext": "text",
                        "type": "readme",
                        "name": readme_name,
                        "lines": f"1-{content.count(chr(10)) + 1}"
                    }
                ))
            break

    # 2. Other text files (Markdown/RST/TXT)
    for text_path in chain(
        repo_path.rglob("*.md"),
        repo_path.rglob("*.rst"),
        repo_path.rglob("*.txt"),
    ):
        if text_path.name.lower() == "readme.md":
            continue
        try:
            content = text_path.read_text(encoding="utf-8").strip()
            if 50 < len(content) < 5000:
                ext = text_path.suffix.lower().lstrip(".") or "txt"  # <- suffix as type/ext
                chunks.append(Document(
                    page_content=content,
                    metadata={
                        "source": str(text_path),
                        "file_ext": ext,     
                        "type": ext,         
                        "name": text_path.name,
                        "lines": f"1-{content.count(chr(10)) + 1}",
                    }
                ))
        except Exception:
            continue

    # 3. Code and misc files
    for filepath in repo_path.rglob("*.*"):
        if is_low_value_file(filepath):
            continue

        try:
            suffix = filepath.suffix.lower()

            # 3a. Python files
            if suffix == ".py":
                code = filepath.read_text(encoding="utf-8")
                tree = ast.parse(code)

                mod_doc = ast.get_docstring(tree)
                if mod_doc and 50 < len(mod_doc) < 5000:
                    chunks.append(Document(
                        page_content=mod_doc,
                        metadata={
                            "source": str(filepath),
                            "file_ext": "code",
                            "type": "module_docstring",
                            "name": filepath.name,
                            "lines": f"1-{code.count(chr(10)) + 1}"
                        }
                    ))

                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                        content = ast.get_source_segment(code, node)
                        if content and 50 < len(content) < 5000:
                            chunks.append(Document(
                                page_content=content,
                                metadata={
                                    "source": str(filepath),
                                    "file_ext": "code",
                                    "type": type(node).__name__.lower(),
                                    "name": node.name,
                                    "lines": f"{node.lineno}-{getattr(node, 'end_lineno', node.lineno)}"
                                }
                            ))

                        doc = ast.get_docstring(node)
                        if doc and 50 < len(doc) < 5000:
                            chunks.append(Document(
                                page_content=doc,
                                metadata={
                                    "source": str(filepath),
                                    "file_ext": "code",
                                    "type": f"{type(node).__name__.lower()}_docstring",
                                    "name": node.name,
                                    "lines": f"{node.lineno}-{getattr(node, 'end_lineno', node.lineno)}"
                                }
                            ))

            # 3b. Notebooks
            elif suffix == ".ipynb":
                nb = nbformat.read(filepath, as_version=4)
                for i, cell in enumerate(nb.cells):
                    if cell.cell_type in ("markdown", "code"):
                        content = cell.source.strip()
                        if 50 < len(content) < 5000:
                            chunks.append(Document(
                                page_content=content,
                                metadata={
                                    "source": str(filepath),
                                    "file_ext": "code",
                                    "type": f"{cell.cell_type}_cell",
                                    "name": f"{filepath.name} - cell {i}",
                                    "lines": f"cell_{i}"
                                }
                            ))

            # 3c. Other code/text files
            else:
                code = filepath.read_text(encoding="utf-8")
                if 50 < len(code) < 5000:
                    chunks.append(Document(
                        page_content=code,
                        metadata={
                            "source": str(filepath),
                            "file_ext": "code",
                            "type": suffix,
                            "name": filepath.name,
                            "lines": f"1-{code.count(chr(10)) + 1}"
                        }
                    ))

        except Exception:
            continue

    return chunks
