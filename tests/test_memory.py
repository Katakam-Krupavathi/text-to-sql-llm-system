import json
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.agent import answer_question, plan
from app.main import app
from app.memory import ConversationMemoryStore, memory_store
from app.validator import validate_and_normalize_sql

client = TestClient(app)


def test_memory_store_turn_tracking():
    store = ConversationMemoryStore(max_turns_per_session=3)
    session_id = "test-session-1"

    store.add_turn(
        session_id=session_id,
        question="Show all customers in UK",
        reasoning_plan="Filter customers by country = 'UK'",
        sql_query="SELECT company_name FROM customers WHERE country = 'UK';",
        answer="There is 1 customer in the UK: Around the Horn.",
    )

    history = store.get_recent_history(session_id, limit=2)
    assert len(history) == 1
    assert history[0].question == "Show all customers in UK"

    formatted = store.format_history_for_prompt(session_id)
    assert "Show all customers in UK" in formatted
    assert "SELECT company_name FROM customers WHERE country = 'UK';" in formatted


@pytest.mark.asyncio
async def test_two_turn_follow_up_conversation():
    """
    Test a 2-turn conversation:
    Turn 1: Asks for total sales by country.
    Turn 2: Follows up with 'now just show me that for Germany' — confirming context is passed and reused.
    """
    session_id = "user-session-42"
    memory_store.clear_session(session_id)

    # --- Turn 1 ---
    turn1_plan = json.dumps({
        "reasoning_plan": "Group orders by ship_country and sum freight charges.",
        "sql_dialect": "postgres",
        "sql_query": "SELECT ship_country, SUM(freight) AS total_freight FROM orders GROUP BY ship_country;",
    })
    turn1_synth = "Total freight by country: Germany is $84.55, Mexico is $77.44, etc."

    # --- Turn 2 ---
    turn2_plan = json.dumps({
        "reasoning_plan": "Follow-up question referring to previous turn. Filter previous freight aggregation specifically to ship_country = 'Germany'.",
        "sql_dialect": "postgres",
        "sql_query": "SELECT ship_country, SUM(freight) AS total_freight FROM orders WHERE ship_country = 'Germany' GROUP BY ship_country;",
    })
    turn2_synth = "The total freight for Germany is $84.55."

    async def mock_execute_sql_handler(query: str, **kwargs):
        is_valid, normalized, err = validate_and_normalize_sql(query, target_dialect="postgres")
        if not is_valid:
            return {"success": False, "rows": [], "columns": [], "error": err, "ast_valid": False, "dialect_valid": False}
        return {
            "success": True,
            "rows": [{"ship_country": "Germany", "total_freight": 84.55}],
            "columns": ["ship_country", "total_freight"],
            "error": None,
            "ast_valid": True,
            "dialect_valid": True,
            "normalized_sql": normalized,
        }

    with patch("app.agent.llm_client.generate", new_callable=AsyncMock) as mock_llm, \
         patch("app.agent.execute_sql", side_effect=mock_execute_sql_handler):

        # Turn 1 execution
        mock_llm.side_effect = [turn1_plan, turn1_synth]
        res1 = await answer_question(
            question="What is the total sales freight by country?",
            session_id=session_id,
        )

        assert res1["success"] is True
        assert res1["session_id"] == session_id
        assert "GROUP BY ship_country" in res1["final_sql"]

        # Turn 2 execution (follow-up)
        mock_llm.side_effect = [turn2_plan, turn2_synth]
        res2 = await answer_question(
            question="Now just show me that for Germany",
            session_id=session_id,
        )

        assert res2["success"] is True
        assert res2["session_id"] == session_id
        assert "Germany" in res2["final_sql"]
        assert "GROUP BY ship_country" in res2["final_sql"]

        # Verify that turn 2 LLM prompt received the history from turn 1
        turn2_call = mock_llm.call_args_list[2]  # Call index 2 is turn 2 plan()
        prompt_sent = turn2_call.kwargs["prompt"]
        assert "Previous Conversation Context:" in prompt_sent
        assert "What is the total sales freight by country?" in prompt_sent


@pytest.mark.asyncio
async def test_session_isolation():
    """Confirms different session_ids maintain separate conversation contexts."""
    store = ConversationMemoryStore()
    store.add_turn("session-A", "Question A", "Plan A", "SELECT 1;", "Answer A")

    history_b = store.format_history_for_prompt("session-B")
    assert history_b == ""  # Session B has no knowledge of Session A


def test_ask_endpoint_with_session_id():
    mock_plan = json.dumps({
        "reasoning_plan": "Count customers",
        "sql_dialect": "postgres",
        "sql_query": "SELECT COUNT(*) FROM customers;",
    })
    mock_synth = "There are 10 customers."

    with patch("app.agent.llm_client.generate", new_callable=AsyncMock) as mock_llm, \
         patch("app.agent.execute_sql", new_callable=AsyncMock) as mock_exec:
        
        mock_llm.side_effect = [mock_plan, mock_synth]
        mock_exec.return_value = {
            "success": True,
            "rows": [{"count": 10}],
            "columns": ["count"],
            "error": None,
            "ast_valid": True,
            "dialect_valid": True,
        }

        response = client.post("/ask", json={"question": "Count customers", "session_id": "api-sess-123"})
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "api-sess-123"
        assert data["answer"] == mock_synth
