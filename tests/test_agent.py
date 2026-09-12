import json
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.agent import (
    plan,
    execute_sql,
    synthesize_answer,
    answer_question,
    validate_is_select_query,
    SAMPLE_SCHEMA,
)
from app.main import app

client = TestClient(app)


# --- Guardrail Tests ---

def test_validate_is_select_query_valid():
    validate_is_select_query("SELECT * FROM customers;")
    validate_is_select_query("WITH cte AS (SELECT 1) SELECT * FROM cte;")


def test_validate_is_select_query_invalid():
    with pytest.raises(ValueError, match="prohibited operation|Security Violation"):
        validate_is_select_query("DELETE FROM customers WHERE customer_id = '1';")

    with pytest.raises(ValueError, match="Multiple SQL statements"):
        validate_is_select_query("SELECT 1; DROP TABLE customers;")

    with pytest.raises(ValueError, match="prohibited operation|Security Violation"):
        validate_is_select_query("DROP TABLE products;")


# --- Core Pipeline Tests ---

@pytest.mark.asyncio
async def test_question_succeeds_first_try():
    """Test a) Question that succeeds on first try with coherent natural language synthesis."""
    mock_plan_json = json.dumps({
        "reasoning_plan": "We need to query products table and count total items.",
        "sql_dialect": "postgres",
        "sql_query": "SELECT COUNT(*) AS total_products FROM products;",
    })
    mock_synthesized = "There are currently 20 products available in the database."

    with patch("app.agent.llm_client.generate", new_callable=AsyncMock) as mock_llm, \
         patch("app.agent.execute_sql", new_callable=AsyncMock) as mock_exec:
        
        # 1st LLM call is plan(), 2nd LLM call is synthesize_answer()
        mock_llm.side_effect = [mock_plan_json, mock_synthesized]
        mock_exec.return_value = {
            "success": True,
            "rows": [{"total_products": 20}],
            "columns": ["total_products"],
            "error": None,
        }

        result = await answer_question("How many products do we have?", max_retries=3)

        assert result["success"] is True
        assert len(result["sql_attempts"]) == 1
        assert result["sql_attempts"][0]["success"] is True
        assert result["final_sql"] == "SELECT COUNT(*) AS total_products FROM products;"
        assert result["rows"] == [{"total_products": 20}]
        assert result["columns"] == ["total_products"]
        assert result["answer"] == mock_synthesized
        # Ensure answer is a coherent sentence and not raw JSON
        assert not result["answer"].startswith("{")
        assert "products" in result["answer"]


@pytest.mark.asyncio
async def test_question_self_correction_on_retry():
    """Test b) Question that requires self-correction (wrong column name on attempt 1, fixed on attempt 2)."""
    # Attempt 1 plan (invalid column `name` instead of `product_name`)
    attempt1_plan = json.dumps({
        "reasoning_plan": "Filter products table by category_id.",
        "sql_dialect": "postgres",
        "sql_query": "SELECT name FROM products WHERE category_id = 1;",
    })
    
    # Attempt 2 plan (corrected column `product_name`)
    attempt2_plan = json.dumps({
        "reasoning_plan": "Corrected column to product_name from products table.",
        "sql_dialect": "postgres",
        "sql_query": "SELECT product_name FROM products WHERE category_id = 1;",
    })

    synthesized_answer = "The products in this category are Chai and Chang."

    with patch("app.agent.llm_client.generate", new_callable=AsyncMock) as mock_llm, \
         patch("app.agent.execute_sql", new_callable=AsyncMock) as mock_exec:
        
        # LLM calls: attempt 1 plan -> attempt 2 plan -> synthesize
        mock_llm.side_effect = [attempt1_plan, attempt2_plan, synthesized_answer]

        # Executions: attempt 1 fails with column error -> attempt 2 succeeds
        mock_exec.side_effect = [
            {
                "success": False,
                "rows": [],
                "columns": [],
                "error": 'column "name" does not exist',
            },
            {
                "success": True,
                "rows": [{"product_name": "Chai"}, {"product_name": "Chang"}],
                "columns": ["product_name"],
                "error": None,
            },
        ]

        result = await answer_question("List products in category 1", max_retries=3)

        assert result["success"] is True
        assert len(result["sql_attempts"]) == 2
        assert result["sql_attempts"][0]["success"] is False
        assert "column \"name\" does not exist" in result["sql_attempts"][0]["error"]
        assert result["sql_attempts"][1]["success"] is True
        assert result["final_sql"] == "SELECT product_name FROM products WHERE category_id = 1;"
        assert len(result["rows"]) == 2
        assert result["answer"] == synthesized_answer


@pytest.mark.asyncio
async def test_question_exhausts_retries_fails_gracefully():
    """Test c) Question that exhausts all retries and fails gracefully."""
    bad_plan = json.dumps({
        "reasoning_plan": "Query nonexistent table.",
        "sql_dialect": "postgres",
        "sql_query": "SELECT * FROM non_existent_table;",
    })

    with patch("app.agent.llm_client.generate", new_callable=AsyncMock) as mock_llm, \
         patch("app.agent.execute_sql", new_callable=AsyncMock) as mock_exec:
        
        # Returns bad plan every time
        mock_llm.side_effect = [bad_plan, bad_plan, bad_plan]

        # Fails execution every time
        mock_exec.return_value = {
            "success": False,
            "rows": [],
            "columns": [],
            "error": 'relation "non_existent_table" does not exist',
        }

        result = await answer_question("Show me data from secret table", max_retries=3)

        assert result["success"] is False
        assert len(result["sql_attempts"]) == 3
        assert result["rows"] == []
        assert "unable to successfully answer your question after 3 attempts" in result["answer"]
        assert 'relation "non_existent_table" does not exist' in result["answer"]


# --- API Endpoint Test ---

def test_ask_endpoint_success():
    mock_plan_json = json.dumps({
        "reasoning_plan": "Query customers in Germany.",
        "sql_dialect": "postgres",
        "sql_query": "SELECT company_name FROM customers WHERE country = 'Germany';",
    })
    mock_synthesized = "The customers located in Germany are Alfreds Futterkiste and Blauer See Delikatessen."

    with patch("app.agent.llm_client.generate", new_callable=AsyncMock) as mock_llm, \
         patch("app.agent.execute_sql", new_callable=AsyncMock) as mock_exec:
        
        mock_llm.side_effect = [mock_plan_json, mock_synthesized]
        mock_exec.return_value = {
            "success": True,
            "rows": [{"company_name": "Alfreds Futterkiste"}, {"company_name": "Blauer See Delikatessen"}],
            "columns": ["company_name"],
            "error": None,
        }

        response = client.post("/ask", json={"question": "Which customers are located in Germany?"})
        assert response.status_code == 200
        data = response.json()
        assert data["answer"] == mock_synthesized
        assert len(data["sql_attempts"]) == 1
        assert data["final_sql"] == "SELECT company_name FROM customers WHERE country = 'Germany';"
        assert len(data["rows"]) == 2
        assert data["columns"] == ["company_name"]
