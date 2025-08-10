from imports import *
from chunking import clone_repo, extract_all_chunks
from save_to_vector_db import save_to_faiss_split_by_ext
from graph import build_graph, SectionSpec

# change to input
repo_url = "https://github.com/adarshlearnngrow/StepUpYourCareer.AI"
repo_path = clone_repo(repo_url)



chunks = extract_all_chunks(repo_path)

stats = save_to_faiss_split_by_ext(chunks, base_dir="docs_index", model="text-embedding-3-small")
print(stats)


app = build_graph()

print("Writing Objective & Scope...")
overview = SectionSpec(
        name="Objective & Scope",
        query="Project goals/objectives and scope or limitations as described in README and docstrings.",
        route="both",
        k_text=12,
        guidance="Include '### Goals' bullets and '### Out of Scope' bullets."
    )

print("Wrote:", app.invoke({"spec": overview})["out_path"])

print("Writing System Architecture...")
architecture = SectionSpec(
        name="System Architecture",
        query="Architecture overview of the project: high-level system architecture and component responsibilities",
        route="both",
        k_text=10,
        k_code=20,
        guidance="Focus on the bigger as well as smaller picture.",
        additional_context=''' 
You are helping write the **System Architecture** section for a technical project.

You will be given:
- Project name & brief description — What the system does.
- Key goals — What it is designed to achieve.
- Key technologies — Languages, frameworks, tools, services.
- Any special constraints — e.g., latency, security, budget.
- Retrieved repository content tagged as architecture, diagrams, component descriptions, configuration files, and tech stack details, requirement.txt.

Output format (only include what is available or inferred):

1. **System Architecture Diagram (Mermaid)**
   - Use `flowchart TD` or `graph LR`.
   - Include the flow of the main applicaiton.
   - Mark the title as Infered from the code.
   - Mark missing elements as (Information not available in repository).

2. **Key Components Table**
   - Columns: Component | Responsibility | Technology | Evidence
   - Write proper technology with package, frameworks, modules, etc.
   - Include component which are acutally used.
   - Only what is in the repo or inferred. Don't assume anything.

3. **Detailed Explanation**
   - Be very detailed about each step techincally. Details like what technique used for example clustering, RAG, few-shot-prompting etc, 
   also if you can you can write in pointwise manner. If you think there is less information about some step, write less information about this but dont skips any step.
   - Explain important python functions too.
   - Also explain the technical method used for example clustering, RAG, few-shot-prompting etc.
   - What kind of data is used for training, validation, testing etc. If json, then show a sample json, only if available. No guessing.
   - Mark missing or inferred steps clearly per rules.

4. **Deployment View**
   - Tell the entire thing for eg. Local dev setup, staging, production topology etc.
   - Mark inferred items clearly per rules.

5. **Scalability & Reliability**
   - Only repo data or clearly marked inference.

6. **Security & Compliance**
   - Authentication, authorization, data protection, logging.
   - Mark inferred items clearly per rules.

7. **Trade-offs & Alternatives**
   - Key design choices with pros/cons.
   - Mark inferred items clearly per rules.

8. **Assumptions & Constraints**
   - Supported use cases, limits, boundaries.
   - Mark inferred items clearly per rules.

9. **Risks & Mitigations**
   - Technical and operational risks with prevention/recovery strategies.
   - Mark inferred items clearly per rules.

10. **Observability & Quality**
    - Metrics, tracing, alerts, testing approach.

11. **Future Extensions**
    - Possible evolutions, integrations, optimizations.
    - Mark inferred items clearly per rules.
'''
)

print("Wrote:", app.invoke({"spec": architecture})["out_path"])

print("Writing Technologies Used...")
technologies = SectionSpec(
        name="Technologies Used",
        query="Installation prerequisites and versions",
        route="both",
        k_text=5,
        k_code=5,
        guidance="""
        Just list the technologies used in a way like
        Languages: Python, JavaScript
        Frameworks: Flask, React
        Packages: NumPy, Pandas
        """,
        additional_context=""
)


print("Wrote:", app.invoke({"spec": technologies})["out_path"])

print("Writing Installation & Setup...")
installation_guide = SectionSpec(
        name="Installation & Setup",
        query="Installation prerequisites, enviornment variables and versions",
        route="both",
        k_text=5,
        k_code=5,
        guidance="Write a step by step guide for installation and setup",
        additional_context=""
)


print("Wrote:", app.invoke({"spec": installation_guide})["out_path"])

print("Writing API Key...")
api_key = SectionSpec(
        name="API Key",
        route="both",
        k_text=5,
        k_code=5,
        query=(
        "API endpoints, FastAPI/Flask routes @app.get @router.post @blueprint.route "
        "openapi swagger schema path operation request response status code "
        "environment variables os.getenv os.environ BaseSettings pydantic dotenv "
        ".env config settings yaml toml json"),
        guidance=(
            "Write a deep, exact section:\n"
            "1) Base URL & API version (if present).\n"
            "2) Auth scheme (key/header/bearer), rate limits if any.\n"
            "3) Endpoints table: Method | Path | Summary | Request | Response | Source tag.\n"
            "4) Environment variables table: NAME | Purpose | Where read (file:lines) | Default/example if visible.\n"
            "5) Example curl for 1–2 key endpoints.\n"
            "Apply strict citation rules: every sentence must end with a single allowed tag. "
            "If info is missing, write (Information not available in repository). "
            "Do NOT invent endpoints or env vars."
        ),
)

print("Wrote:", app.invoke({"spec": api_key})["out_path"])
