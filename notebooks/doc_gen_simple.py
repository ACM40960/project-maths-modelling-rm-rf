# docgen_inferenceclient.py
#
# Requirements:
#   pip install gitpython huggingface_hub sentence-transformers tqdm
# One-time: run `huggingface-cli login` so your token is cached.

import os, glob
from git import Repo
from tqdm import tqdm
from sentence_transformers import SentenceTransformer
from huggingface_hub import InferenceClient

# ——— 1. CONFIG ——————————————————————————————————————————————————
REPO_URL    = "https://github.com/psf/requests"
LOCAL_DIR   = "tmp_repo"
EMBED_MODEL = "all-MiniLM-L6-v2"            # ~80 MB
LLM_MODEL   = "mosaicml/mpt-7b-instruct"    # supports text-generation
SECTIONS    = ["Overview", "Dependencies", "File Structure", "Key API Summary"]
NUM_FILES   = 3     # how many .py files to include
SNIP_LEN    = 2000  # chars per file
# ——————————————————————————————————————————————————————————————

# ——— 2. CLONE REPO ———————————————————————————————————————————————
if not os.path.isdir(LOCAL_DIR):
    print(f"Cloning {REPO_URL}…")
    Repo.clone_from(REPO_URL, LOCAL_DIR, depth=1)
else:
    print(f"Using existing folder {LOCAL_DIR}")

# ——— 3. READ & PREPARE SNIPPETS —————————————————————————————————————
py_files = glob.glob(f"{LOCAL_DIR}/**/*.py", recursive=True)[:NUM_FILES]
snippets = []
for path in tqdm(py_files, desc="Reading code"):
    try:
        code = open(path, encoding="utf-8").read()
    except:
        continue
    snippet = code[:SNIP_LEN].strip()
    if snippet:
        rel = os.path.relpath(path, LOCAL_DIR)
        snippets.append(f"### {rel}\n```python\n{snippet}\n```")
combined_code = "\n\n".join(snippets)

# ——— 4. DEPENDENCIES ————————————————————————————————————————————————
req_path = os.path.join(LOCAL_DIR, "requirements.txt")
if os.path.exists(req_path):
    deps = open(req_path, encoding="utf-8").read().splitlines()
else:
    deps = ["(no requirements.txt found)"]

# ——— 5. DIRECTORY TREE —————————————————————————————————————————————
tree_lines = []
for root, dirs, files in os.walk(LOCAL_DIR):
    level = root.replace(LOCAL_DIR, "").count(os.sep)
    indent = "  " * level
    tree_lines.append(f"{indent}- {os.path.basename(root)}/")
    for fn in files:
        tree_lines.append(f"{indent}  - {fn}")
file_tree = "\n".join(tree_lines)

# ——— 6. OPTIONAL: EMBEDDINGS (for future RAG) ——————————————————————
embedder = SentenceTransformer(EMBED_MODEL)  # local ~80 MB download

# ——— 7. INFERENCE CLIENT —————————————————————————————————————————————
client = InferenceClient(model=LLM_MODEL)    # token auto-loaded via CLI login

# ——— 8. PROMPT TEMPLATES ————————————————————————————————————————————
def make_prompt(section: str) -> str:
    if section == "Overview":
        return (
            "You are a technical writer. Write an **Overview** of this codebase "
            "based only on the following code snippets:\n\n"
            + combined_code
        )
    if section == "Dependencies":
        deps_md = "\n".join(f"- {d}" for d in deps)
        return (
            "You are a technical writer. List and briefly describe the project's "
            "dependencies below:\n\n" + deps_md
        )
    if section == "File Structure":
        return (
            "You are a technical writer. Describe the file and folder structure "
            "for this project, based on this directory tree:\n\n" + file_tree
        )
    if section == "Key API Summary":
        return (
            "You are a technical writer. From these code snippets, extract and "
            "explain the key public functions and classes (name, signature, purpose):\n\n"
            + combined_code
        )
    return ""

# ——— 9. GENERATE & SAVE ——————————————————————————————————————————————
docs = ["# Technical Documentation\n"]
for sec in SECTIONS:
    print(f"⏳ Generating section: {sec}")
    prompt = make_prompt(sec)
    result = client.text_generation(
        prompt,
        max_new_tokens=512,
        temperature=0.2,
        top_p=0.9,
        return_full_text=False
    )
    docs.append(f"## {sec}\n\n{result.generated_text.strip()}\n")

output = "Shark_tank.md"
with open(output, "w", encoding="utf-8") as f:
    f.write("\n".join(docs))

print(f"\n✅ Documentation written to {output}")
