import streamlit as st
import os
import sys
from pathlib import Path
import traceback
import pypandoc
import subprocess
from streamlit_mermaid import st_mermaid
import shutil
import stat # <-- ADDED: Needed for changing file permissions

# --- Import your existing functions ---
# Make sure these files are in the same directory as this app.py
from imports import *
from chunking import clone_repo, extract_all_chunks
from save_to_vector_db import save_to_faiss_split_by_ext
from graph import build_graph, SectionSpec

# --- NEW: Error handler for shutil.rmtree on Windows ---
def handle_rmtree_error(func, path, exc_info):
    """
    Error handler for shutil.rmtree.

    If the error is a permission error, it attempts to change the
    file's permissions and then retries the deletion.
    """
    # Check if the error is a permission error
    if not os.access(path, os.W_OK):
        # Try to change the permission to writable
        os.chmod(path, stat.S_IWUSR)
        # Retry the function that failed (e.g., os.unlink)
        func(path)

# --- One-time setup for Streamlit Cloud ---
if 'setup_complete' not in st.session_state:
    try:
        if not os.path.exists('./setup_done.flag'):
            st.toast("Performing first-time setup for cloud environment...")
            if os.path.exists('streamlit_app_setup.sh'):
                subprocess.run(['chmod', '+x', 'streamlit_app_setup.sh'], check=True)
                subprocess.run(['./streamlit_app_setup.sh'], check=True)
                with open('./setup_done.flag', 'w') as f:
                    f.write('Setup complete')
                st.toast("Setup complete! The app is ready.")
            else:
                st.toast("Setup script not found, assuming local environment.")
        st.session_state.setup_complete = True
    except Exception as e:
        st.warning(f"Could not perform cloud setup: {e}")
        st.session_state.setup_complete = True


# --- Document Conversion Functions ---

def collate_markdown_files(md_file_paths: list, output_file: str, title: str = "Project Documentation"):
    """
    Combines the raw content from a list of .md files into a single file in the given order.
    """
    with open(output_file, "w", encoding="utf-8") as outfile:
        # Add the main document title
        outfile.write(f"# {title}\n\n")
        for i, md_file_path_str in enumerate(md_file_paths):
            with open(md_file_path_str, "r", encoding="utf-8") as infile:
                outfile.write(infile.read())
            # Add a separator, but not after the very last file
            if i < len(md_file_paths) - 1:
                outfile.write("\n\n---\n\n")
    return output_file

def convert_md_to_docx(source_md: str, output_docx: str):
    """
    Converts a markdown file to a .docx Word document, handling paths for local and cloud environments.
    """
    filter_executable = 'mermaid-filter' # Default for Linux/Cloud

    if sys.platform == "win32":
        npm_prefix = os.path.expanduser("~\\AppData\\Roaming\\npm")
        win_path = os.path.join(npm_prefix, "mermaid-filter.cmd")
        if os.path.exists(win_path):
            filter_executable = win_path
        else:
            st.error(f"Mermaid filter not found at the expected Windows path: {win_path}")
            return None
    
    extra_args = ['--filter', filter_executable, '-s']
    
    try:
        pypandoc.convert_file(
            source_md, 'docx', outputfile=output_docx, extra_args=extra_args
        )
        return output_docx
    except Exception as e:
        st.error(f"Error during Word document conversion: {e}")
        st.warning("Please ensure Pandoc and mermaid-filter are installed and accessible.")
        return None

# --- Streamlit App UI ---
st.set_page_config(page_title="RepoDoc AI", layout="wide")
st.title("🤖 RepoDoc AI: Automated Repository Documentation Generator")
st.markdown("Enter a public GitHub repository URL to automatically generate technical documentation.")

# Initialize session state variables
if 'generation_complete' not in st.session_state:
    st.session_state.generation_complete = False
if 'generated_files' not in st.session_state:
    st.session_state.generated_files = []
if 'repo_path' not in st.session_state:
    st.session_state.repo_path = ""

repo_url = st.text_input(
    "GitHub Repository URL",
    placeholder="https://github.com/langchain-ai/langchain",
    key="repo_url_input"
)

if st.button("Generate Documentation", type="primary"):
    if not repo_url:
        st.warning("Please enter a GitHub repository URL to start.")
    else:
        st.session_state.generation_complete = False
        st.session_state.generated_files = []
        
        with st.status("Generating documentation... 🚀", expanded=True) as status:
            try:
                # --- Step 1: Clone Repo and Rename Folder ---
                status.update(label="Cloning repository...")
                st.write(f"Cloning {repo_url}...")
                
                temp_repo_path_str = clone_repo(repo_url)
                
                repo_name_from_url = repo_url.split('/')[-1]
                if repo_name_from_url.endswith('.git'):
                    repo_name_from_url = repo_name_from_url[:-4]
                    
                clones_parent_dir = Path("cloned_repos")
                clones_parent_dir.mkdir(exist_ok=True)
                
                repo_path = clones_parent_dir / repo_name_from_url
                
                # If the target directory already exists, remove it to ensure a clean clone
                if repo_path.exists():
                    # --- CHANGED: Use the error handler to forcefully remove the directory ---
                    shutil.rmtree(repo_path, onerror=handle_rmtree_error)
                    
                os.rename(temp_repo_path_str, repo_path)
                
                st.session_state.repo_path = str(repo_path)
                st.write(f"✅ Repository cloned to: `{repo_path}`")

                # --- Subsequent Steps ---
                status.update(label="Extracting code and text chunks...")
                chunks = extract_all_chunks(str(repo_path))
                st.write(f"✅ Extracted {len(chunks)} chunks.")

                status.update(label="Embedding and saving to vector database...")
                stats = save_to_faiss_split_by_ext(chunks, base_dir="docs_index", model="text-embedding-3-small")
                st.write("✅ Chunks saved to FAISS vector index.")

                status.update(label="Initializing generation graph...")
                app = build_graph()
                st.write("✅ LangGraph application built.")
                
                repo_name = Path(st.session_state.repo_path).name

                specs_to_generate = [
                     SectionSpec(
                         name="Objective & Scope", 
                         query="Project goals/objectives and scope.", 
                         guidance="Generate a comprehensive and detailed 'Objective & Scope' section. Use '### Goals' and '### Out of Scope' subheadings. Elaborate on each point.",
                         route="both", k_text=15
                     ),
                     SectionSpec(
                         name="System Architecture", 
                         query="High-level system architecture, data flow, component responsibilities, and deployment strategy.", 
                         guidance="Generate a highly detailed 'System Architecture' section based on the extensive context provided. Follow the specified multi-part format precisely.",
                         route="both", k_text=20, k_code=35,
                         additional_context='''
You are an expert technical writer creating the **System Architecture** section of a software design document. Your output must be exhaustive and follow this precise format. Base your answers ONLY on the provided repository content.

1.  **System Architecture Diagram (Mermaid)**
    -   Create a `graph TD` or `flowchart TD` Mermaid diagram illustrating the primary data and logic flow.
    -   Title the diagram appropriately. Mark it as "(Inferred from code)" if necessary.
    -   If a key component's existence is unclear from the repo, mark it as "(Information not available in repository)".

2.  **Key Components Table**
    -   Create a markdown table with columns: `Component | Responsibility | Technology | Evidence`.
    -   List each major functional part of the system (e.g., data processing, API, recommendation engine).
    -   Provide a concise responsibility for each.
    -   List the specific technologies, frameworks, or libraries used.
    -   Cite the file(s) that provide evidence for this component.

3.  **Detailed Explanation**
    -   Provide a detailed, technical explanation of the system's workflow, referencing the components from the table and diagram.
    -   Explain key algorithms or techniques used (e.g., RAG, clustering, specific data transformations).
    -   Describe important functions and their roles.
    -   If sample data (like a JSON object) is available, show a small, illustrative example. Do not invent data.

4.  **Deployment View**
    -   Describe the likely deployment topology (e.g., local development setup, potential staging/production environments). Mark inferred information clearly.

5.  **Scalability & Reliability**
    -   Analyze how the system might scale. Mention any design choices that support or hinder scalability or reliability. Mark inferred information clearly.

6.  **Security & Compliance**
    -   Discuss any visible security measures (authentication, data protection) or lack thereof. Mark inferred information clearly.
'''
                     ),
                     SectionSpec(
                         name="Technologies Used", 
                         query="All technologies, libraries, packages, and frameworks used.", 
                         guidance="Generate an exhaustive 'Technologies Used' section. List all items found in dependency files (like requirements.txt, package.json, etc.) and categorize them by type (Languages, Frameworks, Key Libraries).",
                         route="both", k_text=10, k_code=10
                     ),
                     SectionSpec(
                         name="Installation & Setup", 
                         query="Installation prerequisites, environment variables, and setup instructions.", 
                         guidance="Generate a detailed, step-by-step 'Installation & Setup' guide. Ensure it includes distinct subsections for 'Prerequisites', 'Environment Setup', and 'Installation Steps'.",
                         route="both", k_text=10, k_code=10
                     ),
                     SectionSpec(
                         name="API & Environment Variables", 
                         query="API endpoints, routes, required environment variables, and configuration details.", 
                         guidance="Generate a precise 'API & Environment Variables' section. Create a table for API endpoints with columns: `Method | Path | Summary`. Create a separate table for Environment Variables with columns: `Variable | Purpose`. Extract all available information.",
                         route="both", k_text=10, k_code=10
                     )
                ]
                
                generated_files_temp = []
                
                progress_bar = st.progress(0, text="Starting document generation...")
                for i, spec in enumerate(specs_to_generate):
                    status.update(label=f"Writing '{spec.name}' section...")
                    result = app.invoke({"spec": spec})
                    
                    original_out_path = Path(result["out_path"])
                    new_filename = f"{repo_name}_{original_out_path.name}"
                    new_out_path = original_out_path.parent / new_filename
                    
                    os.rename(original_out_path, new_out_path)
                    
                    generated_files_temp.append(str(new_out_path))
                    
                    progress_bar.progress((i + 1) / len(specs_to_generate), text=f"Generated '{spec.name}'")
                
                st.session_state.generated_files = generated_files_temp
                st.session_state.generation_complete = True
                status.update(label="Documentation generated successfully!", state="complete", expanded=False)

            except Exception as e:
                status.update(label="An error occurred!", state="error")
                st.error(f"An unexpected error occurred: {e}")
                st.code(traceback.format_exc())
                st.session_state.generation_complete = False

# --- Display Markdown and Download Button AFTER generation is complete ---
if st.session_state.generation_complete:
    st.success("🎉 All documentation sections have been generated!")
    
    repo_name_for_title = Path(st.session_state.repo_path).name
    st.header(f"Preview for: {repo_name_for_title}")

    for file_path in st.session_state.generated_files:
        st.divider() 
        try:
            with open(file_path, 'r', encoding="utf-8") as f:
                content = f.read()

                if "```mermaid" in content:
                    parts = content.split("```mermaid")
                    st.markdown(parts[0], unsafe_allow_html=True)
                    
                    mermaid_code_and_after = parts[1]
                    mermaid_code = mermaid_code_and_after.split("```")[0].strip()
                    
                    st.code(mermaid_code, language='mermaid') 
                    st_mermaid(mermaid_code, height="600px")
                    
                    after_diagram_parts = mermaid_code_and_after.split("```", 1)
                    if len(after_diagram_parts) > 1 and after_diagram_parts[1].strip():
                        st.markdown(after_diagram_parts[1], unsafe_allow_html=True)
                
                else:
                    st.markdown(content, unsafe_allow_html=True)

        except Exception as e:
            st.error(f"Could not read or render file {file_path}: {e}")

    # --- Generate and Offer Word Document for Download ---
    st.divider()
    st.subheader("Download Full Document")

    with st.spinner("Preparing Word document..."):
        repo_name = Path(st.session_state.repo_path).name
        collated_md_path = f"{repo_name}_collated_docs.md"
        final_docx_path = f"{repo_name}_technical_doc.docx"

        collate_markdown_files(
            md_file_paths=st.session_state.generated_files,
            output_file=collated_md_path,
            title=f"{repo_name} Technical Documentation"
        )
        
        docx_file = convert_md_to_docx(collated_md_path, final_docx_path)
        
        if docx_file and os.path.exists(docx_file):
            with open(docx_file, "rb") as f:
                st.download_button(
                    label="✅ Download Technical Doc (.docx)",
                    data=f,
                    file_name=Path(final_docx_path).name,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
        else:
            st.error("Could not generate the Word document for download.")
