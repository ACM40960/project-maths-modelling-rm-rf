from __future__ import annotations
from imports import *
import re
from collections import defaultdict

BASE_DIR    = "docs_index"                 # text_index/ and code_index/ live here
EMBED_MODEL = "text-embedding-3-small"     # must match what you built FAISS with

# Retrieval and prompt defaults
PER_FILE_CAP = 2           # default cap per file unless overridden in cap_for
SNIPPET_CHARS = 700        # max chars per snippet included in CONTEXT
MAX_CONTEXT_CHARS = 6000   # overall CONTEXT budget

# Prefer typical code component names slightly when ranking code hits
_CODE_NAME_BOOST = re.compile(r"(service|api|client|handler|router|controller|model|view|util|utils)", re.I)

# Treat these types as documentation-like for scoring priority
_DOC_TYPES = {"module_docstring", "functiondef_docstring", "classdef_docstring"}

# Prompt fragments used in writer
rules = """Allowed sources:
1) Retrieved repository content.
2) Logical inferences ONLY when the repo doesn’t state the info.
3) Optional contextual/literature knowledge (must be marked).

Citation rules (every sentence MUST end with one or more citation tags):
- Summarising/rephrasing repo info → [the <file_name>:<line_range>] .
  If citing several repo sources, either:
  • put multiple base filenames inside ONE tag, comma-separated
    e.g., [the parser.py:12-40, lexer.py:1-30]
  • OR append multiple tags separated by a single space
    e.g., [the models.py:10-28] [the schema.py:3-20]
- New detail logically deduced → (Inferred from LLM based on repository content) plus one or more repo tags
    e.g., (Inferred from LLM based on repository content) [the service.py:15-60, config.py:1-40]
- External/contextual knowledge → (Included from contextual knowledge, not from repository)
- Missing info → (Information not available in repository)

Important:
- Do NOT mark summarised repo content as inferred.
- Use ONLY base filenames present in CONTEXT (no directories).
- No punctuation after the final closing bracket/parenthesis.
- Mermaid code blocks can be untagged, but add a one-line caption WITH a tag after the block.
- Be concise and factual.
"""

examples = """Examples:
The project exposes a command-line interface for simulations [the README.md:1-80]
The scheduler pulls tasks from the queue and commits results to storage [the scheduler.py:20-70, storage.py:10-55]
The controller coordinates the dealer and gambler roles (Inferred from LLM based on repository content) [the game_controller.py, configuration.py]
External monitoring integration is not described (Information not available in repository)
FastAPI is a Python web framework (Included from contextual knowledge, not from repository)
"""

class State(TypedDict, total=False):
    """
    Workflow state for section generation with optional LLM review.

    Keys:
        spec (SectionSpec): Section requirements/specification.
        context (str): Additional context the writer should consider.
        draft (str): Current draft text for the section.
        out_path (str): Destination path for the finalized section.
        review_mode (Literal["none", "llm"]): Review strategy before saving.
        _judge (str): Raw JSON string from the LLM judge step (internal).
        _human_notes (str): Optional short note from human review (internal).
        retries (int): Count of completed revise loops.
        max_retries (int): Maximum allowed revise loops.

    Notes:
        - With `total=False`, all fields are optional at the type level; mark truly
          required fields with `typing.Required` on Python 3.11+.
        - Keep the invariant: 0 <= retries <= max_retries.
        - When review_mode == "none", judge data can be omitted.
    """
    spec: 'SectionSpec'
    context: str
    draft: str
    out_path: str
    review_mode: Literal["none", "llm"]  # how to review before save
    _judge: str            # JSON string from n_judge
    retries: int           # how many revise→judge loops have run
    max_retries: int       # cap to avoid infinite loop (e.g., 2)

@dataclass
class SectionSpec:
    """
    Authoring/retrieval spec for generating a single document section.

    Fields:
        name: Human-readable section name (e.g., "Introduction").
        query: Retrieval query used to fetch supporting context.
        route: Retrieval route:
               - "text": search only the TEXT index.
               - "both": search TEXT and CODE indexes.
        k_text: Top-K passages to fetch from the TEXT index.
        k_code: Top-K passages to fetch from the CODE index (used when route="both").
        guidance: Optional writing hints/style/constraints for the writer.
        additional_context: Optional extra context appended to the prompt.

    Notes:
        - For purely narrative sections, keep `route="text"`.
        - For API/SDK/reference sections, set `route="both"` and tune `k_code`.
        - `k_text`/`k_code` should be small enough to avoid prompt bloat.
    """
    name: str
    query: str
    route: Literal["text", "both"] = "text"   # "text" or "both (text+code)"
    k_text: int = 5
    k_code: int = 15
    guidance: str = ""                        # optional writing hints
    additional_context: str = ""              # optional additional context

def _score_code_hit(d) -> int:
    src = Path(d.metadata.get("source","")).name
    t   = (d.metadata.get("type") or "").lower()
    s = 0
    if src.endswith(".py"): s += 1
    if _CODE_NAME_BOOST.search(src): s += 2
    if t in _DOC_TYPES: s += 3          # prefer docstrings (high info density)
    return s

def _retrieve(spec: SectionSpec) -> str:
    """Code-first retrieval (when route='both'), per-file cap, char budget, strict [the file:lines] tags."""
    emb = OpenAIEmbeddings(model=EMBED_MODEL)
    parts: List[str] = []
    per_file = defaultdict(int)
    seen = set()               # (src, lines)
    total = 0

    def cap_for(src: str) -> int:
        if src == "README.md": return 1
        if src == "requirements.txt": return 1
        # allow code files a bit more room
        if src.endswith(".py"): return 3
        return PER_FILE_CAP

    def try_add(d):
        nonlocal total
        src = Path(d.metadata.get("source","")).name
        loc = d.metadata.get("lines","")
        key = (src, loc)
        if key in seen or per_file[src] >= cap_for(src): 
            return
        snippet = (d.page_content or "")[:SNIPPET_CHARS]
        entry = f"[the {src}:{loc}] {snippet}"
        if total + len(entry) > MAX_CONTEXT_CHARS:
            return
        parts.append(entry)
        seen.add(key)
        per_file[src] += 1
        total += len(entry)

    # load stores
    text_vs = FAISS.load_local(f"{BASE_DIR}/text_index", emb, allow_dangerous_deserialization=True)

    # --- CODE FIRST (only if asked for both) ---
    if spec.route == "both":
        try:
            code_vs = FAISS.load_local(f"{BASE_DIR}/code_index", emb, allow_dangerous_deserialization=True)
            c_hits = code_vs.max_marginal_relevance_search(spec.query, k=spec.k_code, fetch_k=min(64, 4*spec.k_code))
            # prioritize by our simple score
            c_hits.sort(key=_score_code_hit, reverse=True)
            for d in c_hits:
                try_add(d)
        except Exception:
            pass

    # --- TEXT afterwards (small number to frame purpose) ---
    t_hits = text_vs.max_marginal_relevance_search(spec.query, k=spec.k_text, fetch_k=min(64, 4*spec.k_text))
    for d in t_hits:
        try_add(d)

    return "\n\n".join(parts)

def n_retrieve(state: State) -> State:
    ctx = _retrieve(state["spec"])
    Path("debug").mkdir(exist_ok=True)
    Path("debug/context.txt").write_text(ctx, encoding="utf-8")
    return {"context": ctx}

def n_write(state: State) -> State:
    """
    Produce a single Markdown section using the provided CONTEXT and strict
    end-of-sentence citation tags.

    The function constructs a system+user prompt that (a) constrains the model to
    use only the given repo CONTEXT and optional `additional_context`, and (b)
    enforces your citation policy (one or more tags at the end of every sentence).
    It makes a single call to the chat model and returns a new state dict with
    the generated `draft`.

    Args:
        state (State): Workflow state containing at least:
            - spec (SectionSpec): name, query, route, k_text/k_code, guidance,
              and optional additional_context.
            - context (str): Retrieved snippets to ground the section. Filenames
              in these snippets must match what the writer cites.

    Returns:
        State: A partial update containing:
            - "draft": The generated Markdown section text with citations.

    Raises:
        KeyError: If required keys (e.g., "spec", "context") are missing.
        Any exception propagated by the underlying chat client.

    Notes:
        - The citation policy allows multiple repo citations per sentence, either
          in a single tag with comma-separated base filenames or as multiple
          adjacent tags separated by a single space.
        - No punctuation is allowed after the final tag/parenthesis on a sentence.
        - The function does not perform post-validation; downstream review/QA
          should verify citation conformity if needed.
    """
    spec = state["spec"]
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    sys = (
        "You are a senior engineer who has wrote this code."
        "Now you are writing an industry level technical documentation." 
        "Use ONLY the provided CONTEXT (and additional_context if present) "
        "and follow the citation rules exactly. Also write the section professionally. No use of"
        "likely, maybe, etc. Be concise and factual."
    )

    usr = (
        f"SECTION: {spec.name}\n"
        f"GOAL: {spec.query}\n"
        f"GUIDANCE: {spec.guidance}\n\n"
        f"RULES:\n{rules}\n\n{examples}\n"
        "CONTEXT (snippets already include tags like [the <file>:<lines>] so you can reuse filenames):\n"
        f"{state['context']}\n\n"
        f"ADDITIONAL CONTEXT (optional):\n{getattr(spec, 'additional_context', '')}\n\n"
        f"Write the '{spec.name}' section in clear Markdown."
    )

    md = llm.invoke([{"role": "system", "content": sys},
                     {"role": "user", "content": usr}]).content.strip()
    return {"draft": md}

def route_after_write(state: State):
    """
    Decide where to go after we have a draft:
      - review_mode == 'none'  → save immediately
      - anything else (default: 'llm') → go to LLM judge
    """
    mode = (state.get("review_mode") or "llm").lower()
    return "save" if mode == "none" else "judge"

def n_judge(state: State) -> State:
    """
    Grade the DRAFT strictly against CONTEXT.
    Returns _judge as a JSON string with keys:
      factual, cites_ok, hallucinated, unsupported_claims[], missing_but_expected[], score (0..1), notes
    """
    spec, ctx, draft = state["spec"], state["context"], state["draft"]
    judge_llm = ChatOpenAI(model='gpt-4o-mini', temperature=0)

    sys = (
        "You are a strict technical reviewer. Judge ONLY using CONTEXT. "
        "Return STRICT JSON matching the schema. Looks for any contradictions, missing information, or unsupported claims, also if citation doesn't make sense."
        "For eg: The application can scale by deploying multiple instances of the Streamlit app to handle increased user load (Inferred from LLM based on repository content) [the requirements.txt:1-10]."
        "The score should follow rubric scoring between 0 and 1."
        "If the claim is not supported by the context, return a score of 0 and a note explaining why."
    )
    usr = (
        'Schema:\n'
        '{"factual":bool,"cites_ok":bool,"hallucinated":bool,'
        '"unsupported_claims":[string],"missing_but_expected":[string],'
        '"score":number,"notes":string, }\n\n'
        f"SECTION: {spec.name}\n\nCONTEXT:\n{ctx}\n\nDRAFT:\n{draft}"
    )

    out = judge_llm.invoke([{"role": "system", "content": sys},
                            {"role": "user", "content": usr}]).content

    # Ensure valid JSON; if not, create a failing verdict so we trigger a revise pass
    try:
        json.loads(out)
    except Exception:
        out = ('{"factual": false, "cites_ok": false, "hallucinated": true, '
               '"unsupported_claims":["Non-JSON judge output"], '
               '"missing_but_expected":[], "score": 0.0, "notes":"Judge did not return JSON"}')

    Path("debug").mkdir(exist_ok=True)
    Path(f"debug/{state['spec'].name}_judge.json").write_text(out, encoding="utf-8")
    return {"_judge": out}

def n_human_review(state: State) -> State:
    # not in use yet, in future work
    """
    Print the draft and ask a human for approval/notes in the console.
    If notes are provided, we will run one revise pass.
    """
    print("\n--- DRAFT PREVIEW ----------------------------------\n")
    print(state["draft"])
    print("\n----------------------------------------------------")
    ans = input("Approve this section? [y/N]: ").strip().lower()
    notes = ""
    if ans not in {"y", "yes"}:
        print("Enter revision notes (single line; optional):")
        notes = input("> ").strip()
    return {"_human_notes": notes or ""}


def n_revise(state: State) -> State:
    """
    One-shot auto-revision using either judge notes or human notes.
    Keeps the SAME CONTEXT to avoid moving targets.
    """
    spec, ctx, draft = state["spec"], state["context"], state["draft"]
    notes = state.get("_human_notes", "")

    # If no human notes, synthesize notes from judge JSON
    if not notes:
        try:
            data = json.loads(state.get("_judge", "") or "{}")
        except Exception:
            data = {}
        issues = "\n".join((data.get("unsupported_claims") or []))
        notes = (data.get("notes", "") + ("\n" + issues if issues else "")).strip()

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)
    sys = "Revise DRAFT to align strictly with CONTEXT. Remove/qualify unsupported claims. Add/adjust citations. Keep it concise."
    usr = (
        f"SECTION: {spec.name}\n\nNOTES:\n{notes}\n\n"
        f"CONTEXT:\n{ctx}\n\nDRAFT:\n{draft}\n\n"
        "Return the improved Markdown only."
    )
    fixed = llm.invoke([{"role": "system", "content": sys},
                        {"role": "user", "content": usr}]).content.strip()
    # increment retry counter
    return {"draft": fixed, "retries": state.get("retries", 0) + 1}

def decide_pass_or_revise(state: State):
    """
    Route based on judge result (and human notes if present).
    - If human left notes → revise once.
    - If judge flags problems or low score → revise up to max_retries.
    - Otherwise → save.
    """
    # Human path: if notes exist, do exactly one revise pass
    if state.get("_human_notes"):
        return "revise"

    # LLM path
    try:
        data = json.loads(state.get("_judge", "") or "{}")
    except Exception:
        return "revise"  # malformed judge → try revise once

    score = float(data.get("score", 0))
    bad = (not data.get("factual", True)) or data.get("hallucinated", False) or (not data.get("cites_ok", True))

    if (not bad) and score >= 0.75:
        return "save"

    if state.get("retries", 0) < state.get("max_retries", 2):
        return "revise"

    # Out of retries; if you want to force manual review, return "human" here
    return "save"

def n_save(state: State) -> State:
    spec = state["spec"]
    out = f"# {spec.name}\n\n{state['draft']}\n"
    Path("docs").mkdir(exist_ok=True)
    fname = spec.name.lower().replace("&","and").replace(" ", "_") + ".md"
    path = f"docs/{fname}"
    Path(path).write_text(out, encoding="utf-8")
    return {"out_path": path}

def build_graph():
    g = StateGraph(State)
    g.add_node("retrieve", n_retrieve)
    g.add_node("write", n_write)
    g.add_node("judge", n_judge)
    g.add_node("revise", n_revise)
    g.add_node("save", n_save)

    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "write")
    g.add_conditional_edges("write", route_after_write, {
        "judge": "judge",
        "save":  "save",
    })
    g.add_edge("revise", "judge")
    g.add_conditional_edges("judge", decide_pass_or_revise, {
        "revise": "revise",
        "save":   "save",
    })
    return g.compile()
