import json
import pytest
from unittest.mock import AsyncMock, patch

from app.retrieval import (
    relevant_schema,
    retrieve_golden_queries,
    retrieval_index,
)
from app.agent import plan
from app.index_schema import rebuild_all_indexes


def test_schema_linking_filters_relevant_tables():
    """Test (a): A question mentioning 'customers and orders' retrieves those tables and excludes unrelated ones."""
    schema_text = relevant_schema("List all customers who placed orders in 2023", top_k=2)

    assert "Table: customers" in schema_text
    assert "Table: orders" in schema_text
    # Unrelated tables should not be in the top 2
    assert "Table: categories" not in schema_text
    assert "Table: employees" not in schema_text


def test_value_hinting_in_schema():
    """Test (b): Schema context includes sample value hints for low-cardinality columns."""
    schema_text = relevant_schema("Show products that are discontinued", top_k=2)

    assert "Table: products" in schema_text
    assert "sample values:" in schema_text
    assert "discontinued" in schema_text


@pytest.mark.asyncio
async def test_few_shot_golden_query_retrieval_and_plan_injection():
    """Test (c): Tricky multi-join question retrieves matching golden query and injects it into plan()."""
    question = "What is the total revenue per product category?"
    
    # 1. Verify golden query retrieval finds the category revenue example
    golden_matches = retrieve_golden_queries(question, top_k=2)
    assert len(golden_matches) >= 1
    found_category_example = any("category" in ex["question"].lower() for ex in golden_matches)
    assert found_category_example is True

    # 2. Verify that plan() receives and incorporates the few-shot examples
    mock_llm_response = json.dumps({
        "reasoning_plan": "Join categories, products, and order_items as shown in the few-shot example.",
        "sql_dialect": "postgres",
        "sql_query": "SELECT c.category_name, SUM(oi.unit_price * oi.quantity * (1 - oi.discount)) AS category_revenue FROM categories c JOIN products p ON c.category_id = p.category_id JOIN order_items oi ON p.product_id = oi.product_id GROUP BY c.category_name ORDER BY category_revenue DESC;",
    })

    with patch("app.agent.llm_client.generate", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_llm_response

        plan_result = await plan(question=question)

        assert plan_result["sql_dialect"] == "postgres"
        assert "categories" in plan_result["sql_query"]
        assert "JOIN products" in plan_result["sql_query"]
        assert "JOIN order_items" in plan_result["sql_query"]

        # Verify that the LLM prompt received the few-shot examples
        call_args = mock_llm.call_args
        prompt_sent = call_args.kwargs["prompt"]
        assert "Here are examples of how similar questions were solved correctly before:" in prompt_sent
        assert "category_revenue" in prompt_sent or "categories" in prompt_sent


@pytest.mark.asyncio
async def test_index_schema_cli_rebuild():
    """Test (d): Verifies the CLI index builder rebuilds all three indexes."""
    with patch("app.index_schema.profile_table_columns", new_callable=AsyncMock) as mock_profile:
        mock_profile.return_value = {
            "products": {"discontinued": [False, True]},
            "customers": {"country": ["Germany", "UK"]},
        }
        await rebuild_all_indexes(verbose=False)
        assert len(retrieval_index.schema_index) > 0
        assert len(retrieval_index.golden_index) > 0
