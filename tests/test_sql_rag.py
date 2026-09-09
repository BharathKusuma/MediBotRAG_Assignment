# pyrefly: ignore [missing-import]
import pytest
from backend.rag.sql_rag import sql_rag_chain, execute_query, is_analytical_question, clean_sql_statement

def test_analytical_question_classifier():
    assert is_analytical_question("How many billing claims were escalated last month?") is True
    assert is_analytical_question("Which equipment category has the most open maintenance tickets?") is True
    assert is_analytical_question("What is the total approved amount for cardiology claims?") is True
    assert is_analytical_question("What is the protocol for administering IV epinephrine?") is False
    assert is_analytical_question("How to calibrate the ultrasound probe?") is False

def test_sql_rag_direct_queries():
    # Test Question 1: Escalated claims count
    q1 = execute_query("SELECT COUNT(*) as count FROM claims WHERE status = 'escalated';")
    assert len(q1) > 0
    assert "count" in q1[0]

    # Test Question 2: Equipment category with most open tickets
    q2 = execute_query(
        "SELECT category, COUNT(*) as open_count FROM maintenance_tickets "
        "WHERE status = 'in_progress' GROUP BY category ORDER BY open_count DESC LIMIT 1;"
    )
    assert len(q2) > 0
    assert "category" in q2[0]

    # Test Question 3: Total approved amount
    q3 = execute_query("SELECT SUM(approved_amount) as total_approved FROM claims WHERE status = 'approved';")
    assert len(q3) > 0
    assert "total_approved" in q3[0]

    # Test Question 4: Maintenance tickets by status
    q4 = execute_query("SELECT status, COUNT(*) as count FROM maintenance_tickets GROUP BY status;")
    assert len(q4) > 0

def test_clean_sql_statement():
    tc1 = "SELECT category, COUNT(*) as open_count FROM maintenance_tickets WHERE status != 'resolved' GROUP BY category;"
    assert clean_sql_statement(tc1) == tc1

    tc2 = "```sql\nSELECT COUNT(*) FROM claims WHERE status = 'escalated';\n```"
    assert clean_sql_statement(tc2) == "SELECT COUNT(*) FROM claims WHERE status = 'escalated';"

    tc3 = "Here is the SQL query:\nSELECT department, SUM(approved_amount) FROM claims GROUP BY department;\nHope this helps!"
    assert clean_sql_statement(tc3) == "SELECT department, SUM(approved_amount) FROM claims GROUP BY department;"

def test_sql_rag_chain_execution():
    # Test allowed role: billing_executive
    res = sql_rag_chain("How many billing claims were escalated?", role="billing_executive")
    assert res["retrieval_type"] == "sql_rag"
    assert res["blocked_by_rbac"] is False
    assert res["sql_query"] is not None
    assert "Error executing" not in res["answer"]
    assert res["data"] is not None
    assert len(res["answer"]) > 0

    # Test allowed role: admin
    res_admin = sql_rag_chain("Which equipment category has the most open maintenance tickets?", role="admin")
    assert res_admin["retrieval_type"] == "sql_rag"
    assert res_admin["blocked_by_rbac"] is False
    assert res_admin["sql_query"] is not None
    assert "Error executing" not in res_admin["answer"]
    assert res_admin["data"] is not None
    assert len(res_admin["data"]) > 0

    # Test disallowed role: nurse
    res_nurse = sql_rag_chain("How many billing claims were approved?", role="nurse")
    assert res_nurse["blocked_by_rbac"] is True
    assert "restricted" in res_nurse["answer"].lower()

    # Test disallowed role: doctor
    res_doctor = sql_rag_chain("What is the total claimed amount for cardiology?", role="doctor")
    assert res_doctor["blocked_by_rbac"] is True
    assert "restricted" in res_doctor["answer"].lower()

if __name__ == "__main__":
    pytest.main(["-v", __file__])
