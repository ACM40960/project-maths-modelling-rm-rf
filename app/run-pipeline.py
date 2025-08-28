# app/run_pipeline.py
import argparse
from chunking import clone_repo, extract_all_chunks
from save_to_vector_db import save_to_faiss_split_by_ext
from graph import build_graph, SectionSpec

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True, help="GitHub repo URL to analyze")
    p.add_argument("--embed", default="text-embedding-3-small")
    args = p.parse_args()

    # 1) clone -> 2) chunk -> 3) index
    repo_path = clone_repo(args.repo)
    chunks = extract_all_chunks(repo_path)
    save_to_faiss_split_by_ext(chunks, base_dir="docs_index", model=args.embed)

    # 4) run your graph to write Markdown into app/docs/
    app = build_graph()

    def run(spec: SectionSpec):
        out = app.invoke({"spec": spec})
        print("Wrote:", out["out_path"], flush=True)

    # Use the same sections you generate now (adjust names/queries if needed)
    run(SectionSpec(
        name="Objective & Scope",
        query="Project goals/objectives and scope or limitations",
        route="both", k_text=12, k_code=8
    ))
    run(SectionSpec(
        name="System Architecture",
        query="Architecture overview and component responsibilities",
        route="both", k_text=10, k_code=20
    ))
    run(SectionSpec(
        name="Technologies Used",
        query="Installation prerequisites and versions",
        route="both", k_text=5, k_code=5
    ))
    run(SectionSpec(
        name="Installation & Setup",
        query="Setup steps, env vars, versions",
        route="both", k_text=6, k_code=6
    ))
    run(SectionSpec(
        name="API Key",
        query="Endpoints (if any), environment variables, auth scheme, rate limits if present",
        route="both", k_text=6, k_code=6
    ))

if __name__ == "__main__":
    main()
