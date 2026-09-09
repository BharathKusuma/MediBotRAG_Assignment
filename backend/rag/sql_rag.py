import sqlite3
import re
from typing import Dict, Any, List, Optional
from backend.config import DB_PATH, SQL_PERMITTED_ROLES
from backend.rag.llm_service import get_llm_service

# Database schema documentation for LLM prompt
DATABASE_SCHEMA_DESCRIPTION = """
Database: SQLite database 'mediassist.db'

Table 1: claims
Columns:
- claim_id (TEXT, Primary Key, e.g. 'CLM-2024-1000')
- patient_id (TEXT, e.g. 'PAT-51347')
- patient_name (TEXT)
- department (TEXT, e.g. 'nephrology', 'cardiology', 'neurology', 'orthopaedics', 'oncology')
- claim_type (TEXT, e.g. 'reimbursement', 'cashless')
- diagnosis_code (TEXT, ICD-10 code, e.g. 'N17.9', 'I21.4')
- insurer (TEXT, e.g. 'New India Assurance', 'Bajaj Allianz', 'Star Health', 'HDFC Ergo', 'ICICI Lombard', 'United India')
- claimed_amount (REAL, numerical value in INR)
- approved_amount (REAL, numerical value in INR or NULL if pending/rejected)
- status (TEXT, e.g. 'approved', 'pending', 'rejected', 'escalated')
- submitted_date (TEXT, YYYY-MM-DD)
- resolved_date (TEXT, YYYY-MM-DD or NULL)

Table 2: maintenance_tickets
Columns:
- ticket_id (TEXT, Primary Key, e.g. 'TKT-2024-2000')
- equipment_name (TEXT, e.g. 'SterilPro 3000', 'DriveFlow IP-200', 'VentiLife 500')
- equipment_id (TEXT, e.g. 'EQ-HC-3588')
- category (TEXT, e.g. 'sterilisation', 'infusion', 'ventilation', 'imaging', 'monitoring')
- campus (TEXT, e.g. 'MediAssist Hyderabad Central', 'MediAssist Bengaluru South')
- issue_type (TEXT, e.g. 'preventive_maintenance', 'sensor_failure', 'battery_replacement', 'calibration_error')
- fault_code (TEXT, e.g. 'F-05', 'F-01', NULL)
- raised_by (TEXT, engineer/technician name)
- raised_date (TEXT, YYYY-MM-DD)
- resolved_date (TEXT, YYYY-MM-DD or NULL)
- status (TEXT, e.g. 'in_progress', 'resolved', 'pending_parts')
- resolution_note (TEXT)
"""

def clean_sql_statement(raw_output: str) -> str:
    """Extracts only the pure executable SQL statement from raw LLM output."""
    cleaned = raw_output.strip()
    
    # 1. Strip markdown code blocks ```sql ... ```
    if "```" in cleaned:
        code_blocks = re.findall(r"```(?:sql)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
        if code_blocks:
            cleaned = code_blocks[0].strip()
        else:
            cleaned = re.sub(r"```[a-zA-Z]*", "", cleaned).replace("```", "").strip()

    # 2. Extract SELECT query from beginning of SELECT to semicolon or end of query block
    select_match = re.search(r"\bSELECT\b", cleaned, re.IGNORECASE)
    if select_match:
        sql = cleaned[select_match.start():].strip()
        # If there is a semicolon, take everything up to the first semicolon
        if ";" in sql:
            sql = sql.split(";")[0].strip()
        else:
            # Strip trailing comments or explanations
            lines = []
            for line in sql.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith(("note:", "explanation:", "where:", "--", "#")):
                    break
                lines.append(line)
            sql = "\n".join(lines).strip()
    else:
        sql = cleaned

    # Strip trailing semicolon or whitespace and ensure single clean trailing semicolon
    sql = sql.rstrip(";").strip() + ";"
    return sql

def validate_safe_sql(sql: str) -> bool:
    """Ensures query is strictly read-only SELECT statement."""
    upper = sql.strip().upper()
    if not upper.startswith("SELECT"):
        return False
    disallowed = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "CREATE", "EXEC", "REPLACE"]
    for word in disallowed:
        if re.search(rf"\b{word}\b", upper):
            return False
    return True

def execute_query(sql: str) -> List[Dict[str, Any]]:
    """Executes validated SQL query on SQLite database and returns dictionary rows."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        result = [dict(row) for row in rows]
        return result
    finally:
        conn.close()

def is_analytical_question(question: str) -> bool:
    """Heuristic classifier to determine if query is an analytical/tabular query for SQL RAG."""
    lower_q = question.lower()
    
    analytical_keywords = [
        "how many", "count of", "total amount", "average", "highest", "lowest",
        "most open", "sum of", "statistics", "breakdown", "claims was", "claims were",
        "maintenance ticket", "tickets by", "status of claims", "pending claims",
        "escalated claims", "approved amount", "claims for", "tickets in progress"
    ]
    
    table_indicators = ["claim", "claims", "ticket", "tickets", "maintenance", "insurer"]
    
    has_stat_word = any(kw in lower_q for kw in analytical_keywords)
    has_table_word = any(tw in lower_q for tw in table_indicators)
    
    # Exclude purely procedural document queries
    procedural_keywords = ["how to", "procedure for", "guide for", "protocol for", "steps to", "what is the policy"]
    is_procedural = any(pk in lower_q for pk in procedural_keywords)
    
    return (has_stat_word and has_table_word) and not is_procedural

def sql_rag_chain(question: str, role: str = "billing_executive") -> Dict[str, Any]:
    """
    Plain Python function implementing the 3-step SQL RAG pipeline:
    1. Translate natural language question into SQL query using LLM.
    2. Clean and extract only the executable SQL statement.
    3. Execute against mediassist.db and synthesize natural language answer with LLM.
    """
    # 0. Role-based access control verification
    if role not in SQL_PERMITTED_ROLES:
        return {
            "answer": (
                f"SQL RAG is restricted to roles with analytical access (`billing_executive`, `admin`). "
                f"Your current role is `{role}`. Please submit document-related questions instead."
            ),
            "sources": [],
            "retrieval_type": "sql_rag",
            "role": role,
            "blocked_by_rbac": True,
            "sql_query": None,
            "data": None
        }

    llm = get_llm_service()

    # Step 1: Translate Question into SQL
    sql_generation_prompt = f"""You are an expert SQLite SQL developer for MediAssist Health Network.
Given the SQLite database schema below, translate the user's natural language question into a single, valid, optimized SQL query.

{DATABASE_SCHEMA_DESCRIPTION}

Rules:
1. Return ONLY valid SQLite SQL.
2. Only write SELECT queries.
3. Use appropriate aggregations (COUNT, SUM, AVG), GROUP BY, and ORDER BY clauses where appropriate.
4. For date filters or monthly comparisons, use SQLite date functions like strftime('%Y-%m', date_col).
5. For status checks, inspect values like 'approved', 'pending', 'rejected', 'escalated', 'in_progress', 'resolved'.

User Question: {question}

SQL Query:"""

    raw_sql_response = llm.generate(
        prompt=sql_generation_prompt,
        system_instruction="You are a specialized SQL translator. Output only the SQL statement."
    )

    # Step 2: Clean the raw LLM output
    cleaned_sql = clean_sql_statement(raw_sql_response)

    if not validate_safe_sql(cleaned_sql):
        return {
            "answer": f"Generated query could not be executed due to security validation: `{cleaned_sql}`",
            "sources": [{"source_document": "mediassist.db", "section_title": "Security Check", "collection": "db"}],
            "retrieval_type": "sql_rag",
            "role": role,
            "blocked_by_rbac": False,
            "sql_query": cleaned_sql,
            "data": None
        }

    # Step 3: Execute SQL and synthesize answer
    try:
        query_results = execute_query(cleaned_sql)
    except Exception as e:
        return {
            "answer": f"Error executing database query: {str(e)}. Generated SQL was: `{cleaned_sql}`",
            "sources": [{"source_document": "mediassist.db", "section_title": "Query Execution", "collection": "db"}],
            "retrieval_type": "sql_rag",
            "role": role,
            "blocked_by_rbac": False,
            "sql_query": cleaned_sql,
            "data": None
        }

    # Synthesize Natural Language Answer
    synthesis_prompt = f"""You are MediBot, an analytical AI assistant for MediAssist Health Network.
A user asked the following analytical question:
"{question}"

The SQL query executed on the relational database was:
`{cleaned_sql}`

The database returned the following result records (JSON):
{query_results}

Provide a concise, professional, and well-structured natural language answer explaining these metrics.
Include specific numbers, totals, or breakdowns in an easy-to-read format."""

    natural_language_answer = llm.generate(
        prompt=synthesis_prompt,
        system_instruction="You are an analytical hospital operations assistant. Summarize the query results accurately."
    )

    return {
        "answer": natural_language_answer,
        "sources": [{
            "source_document": "mediassist.db",
            "section_title": f"SQL Query: {cleaned_sql}",
            "collection": "database"
        }],
        "retrieval_type": "sql_rag",
        "role": role,
        "blocked_by_rbac": False,
        "sql_query": cleaned_sql,
        "data": query_results
    }
