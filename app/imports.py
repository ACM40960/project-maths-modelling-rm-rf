# Standard library
import ast
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import List, Literal, TypedDict

# Third-party
import nbformat
import pandas as pd
from docx import Document as DocxDocument
from git import Repo, GitCommandError
from openai import OpenAI
from langgraph.graph import StateGraph, END
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from dotenv import load_dotenv, find_dotenv

# Load variables from .env into process environment (e.g., OPENAI_API_KEY)
# Use find_dotenv so it works even if the CWD is app/ or elsewhere
_env_path = find_dotenv(usecwd=True)
if _env_path:
    load_dotenv(_env_path)

client = OpenAI()