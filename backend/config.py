import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "mediassist_data"
DB_PATH = DATA_DIR / "db" / "mediassist.db"
QDRANT_STORAGE_PATH = BASE_DIR / "qdrant_db"

COLLECTION_NAME = "mediassist_knowledge"

ROLES = ["doctor", "nurse", "billing_executive", "technician", "admin"]

ROLE_COLLECTIONS = {
    "doctor": ["clinical", "nursing", "general"],
    "nurse": ["nursing", "general"],
    "billing_executive": ["billing", "general"],
    "technician": ["equipment", "general"],
    "admin": ["general", "clinical", "nursing", "billing", "equipment"]
}

SQL_PERMITTED_ROLES = ["billing_executive", "admin"]

DEMO_USERS = {
    "dr.mehta": {"password": "password123", "role": "doctor", "name": "Dr. Rajesh Mehta", "department": "Clinical Cardiology"},
    "nurse.priya": {"password": "password123", "role": "nurse", "name": "Priya Sharma (RN)", "department": "ICU & Patient Care"},
    "billing.ravi": {"password": "password123", "role": "billing_executive", "name": "Ravi Kumar", "department": "Billing & Insurance"},
    "tech.anand": {"password": "password123", "role": "technician", "name": "Anand Verma", "department": "Biomedical Engineering"},
    "admin.sys": {"password": "password123", "role": "admin", "name": "System Administrator", "department": "Executive / IT"}
}

COLLECTION_ACCESS_MAPPING = {
    "general": ["doctor", "nurse", "billing_executive", "technician", "admin"],
    "clinical": ["doctor", "admin"],
    "nursing": ["nurse", "doctor", "admin"],
    "billing": ["billing_executive", "admin"],
    "equipment": ["technician", "admin"]
}

DENSE_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
SPARSE_EMBEDDING_MODEL = "Qdrant/bm25"
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

TOP_K_RETRIEVAL = 10
TOP_K_RERANKED = 3

# LLM Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto") # auto, gemini, groq, openai, mock
