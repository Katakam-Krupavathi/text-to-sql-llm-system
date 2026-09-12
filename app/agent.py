import json
import logging
import re
from typing import Any, Dict, List, Optional
from sqlalchemy import text
from app.config import settings
from app.db import readonly_engine
from app.llm import llm_client
from app.retrieval import relevant_schema, retrieve_golden_queries, DEFAULT_TABLE_SCHEMAS

# Backward compatibility alias
SAMPLE_SCHEMA = "\n\n".join([d["ddl"] for d in DEFAULT_TABLE_SCHEMAS.values()])

logger = logging.getLogger(__name__)


def clean_json_response(raw_text: str) -> dict:
    """Extracts and parses JSON from model output, stripping any markdown backticks."""
    text_content = raw_text.strip()
    if text_content.startswith("```"):
        text_content = re.sub(r"^```(?:json)?\s*", "", text_content, flags=re.MULTILINE)
        text_content = re.sub(r"\s*```$", "", text_content, flags=re.MULTILINE)
    text_content = text_content.strip()

    try:
        return json.loads(text_content)
    except json.JSONDecodeError:
        start = text_content.find("{")
        end = text_content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text_content[start : end + 1])
        raise ValueError(f"Could not parse valid JSON from LLM output: {raw_text[:200]}")


async def plan(
    question: str,
    schema: Optional[str] = None,
    few_shot_examples: Optional[List[dict]] = None,
    error_context: Optional[str] = None,
) -> dict:
    """Step 1: Calls LLM to produce a structured plan and SQL query with schema-linking and few-shot grounding."""
    # Retrieve relevant schema with value hints if not provided explicitly
    if not schema:
        schema = relevant_schema(question, top_k=5)

    # Retrieve golden few-shot examples if not provided
    if few_shot_examples is None:
        few_shot_examples = retrieve_golden_queries(question, top_k=2)

    system_prompt = f"""You are an expert SQL engineer. Your target dialect is {settings.SQL_DIALECT.upper()}.
Given a relevant database schema (including column types and sample distinct values) and a user question, your task is to:
1. Reason step-by-step in `reasoning_plan` about which tables, joins, columns, filters, and aggregations are required.
2. Ground user business terms using the provided column sample values (e.g. if the user asks for 'discontinued products', look for boolean/status column values).
3. Produce a valid, syntactically correct {settings.SQL_DIALECT.upper()} SELECT query in `sql_query`.

Rules:
- Generate ONLY SELECT or WITH ... SELECT queries. Never generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, etc.
- Return ONLY a valid JSON object with the following structure:
{{
  "reasoning_plan": "Short chain-of-thought describing tables, joins, filters needed before writing SQL.",
  "sql_dialect": "{settings.SQL_DIALECT}",
  "sql_query": "SELECT ...;"
}}
Do NOT wrap output in anything other than the JSON object.
"""

    examples_block = ""
    if few_shot_examples:
        examples_text = []
        for i, ex in enumerate(few_shot_examples, 1):
            examples_text.append(
                f"Example {i}:\n"
                f"Question: {ex['question']}\n"
                f"Reasoning: {ex.get('reasoning', '')}\n"
                f"SQL: {ex['sql']}"
            )
        examples_block = "Here are examples of how similar questions were solved correctly before:\n" + "\n\n".join(examples_text) + "\n\n"

    prompt = f"""{examples_block}Relevant Database Schema & Sample Values:
{schema}

User Question:
{question}
"""
    if error_context:
        prompt += f"""
Previous Attempt Failed with Error:
{error_context}

Please review the error carefully, diagnose what went wrong in your previous reasoning or column/table names, and produce a corrected plan and SQL query.
"""

    raw_response = await llm_client.generate(prompt=prompt, system_prompt=system_prompt)
    data = clean_json_response(raw_response)

    if "sql_query" not in data or "reasoning_plan" not in data:
        raise ValueError(f"Model output missing required fields: {data}")

    data.setdefault("sql_dialect", settings.SQL_DIALECT)
    return data


def validate_is_select_query(query: str) -> None:
    """Ensures the query is strictly a read-only SELECT statement."""
    cleaned = query.strip()
    cleaned = re.sub(r"^--.*$", "", cleaned, flags=re.MULTILINE).strip()
    cleaned = re.sub(r"^/\*.*?\*/", "", cleaned, flags=re.DOTALL).strip()

    statements = [s.strip() for s in cleaned.split(";") if s.strip()]
    if len(statements) > 1:
        raise ValueError("Multiple SQL statements in a single execution are prohibited.")

    first_stmt = statements[0] if statements else ""
    first_token = first_stmt.split()[0].upper() if first_stmt.split() else ""

    if first_token not in ("SELECT", "WITH"):
        raise ValueError(
            f"Only read-only SELECT queries are allowed. Forbidden statement type: {first_token}"
        )

    forbidden_patterns = [
        r"\bINSERT\s+INTO\b",
        r"\bUPDATE\s+",
        r"\bDELETE\s+FROM\b",
        r"\bDROP\s+",
        r"\bALTER\s+",
        r"\bTRUNCATE\s+",
        r"\bGRANT\s+",
        r"\bREVOKE\s+",
        r"\bEXEC\s+",
        r"\bEXECUTE\s+",
    ]
    for pattern in forbidden_patterns:
        if re.search(pattern, first_stmt, re.IGNORECASE):
            raise ValueError(f"Query contains forbidden operation matching: {pattern}")


async def execute_sql(query: str, max_rows: int = 500, timeout_seconds: int = 5) -> dict:
    """Step 2: Runs the query against the read-only DB connection with safety constraints."""
    try:
        validate_is_select_query(query)
    except ValueError as e:
        return {
            "success": False,
            "rows": [],
            "columns": [],
            "error": str(e),
        }

    try:
        async with readonly_engine.connect() as conn:
            try:
                await conn.execute(text(f"SET LOCAL statement_timeout = {timeout_seconds * 1000}"))
            except Exception:
                pass

            result = await conn.execute(text(query))
            columns = list(result.keys()) if result.returns_rows else []
            raw_rows = result.fetchmany(max_rows) if result.returns_rows else []

            rows = [dict(zip(columns, row)) for row in raw_rows]

            return {
                "success": True,
                "rows": rows,
                "columns": columns,
                "error": None,
            }
    except Exception as e:
        logger.warning(f"SQL execution error for query '{query}': {e}")
        return {
            "success": False,
            "rows": [],
            "columns": [],
            "error": str(e),
        }


async def synthesize_answer(question: str, columns: List[str], rows: List[Dict[str, Any]]) -> str:
    """Step 4: Produces a natural-language answer summarizing the query result."""
    system_prompt = """You are a helpful data analyst. Given a user's question and the SQL query results, synthesize a clear, direct, and concise natural language answer.
- Answer the user's question directly in standard English.
- Format numbers, currencies, and dates nicely.
- Do not output raw JSON or SQL unless requested.
- If the result set is empty, state clearly that no matching records were found.
"""
    preview_rows = rows[:20]
    total_count = len(rows)

    prompt = f"""User Question: {question}

Query Columns: {columns}
Query Results ({total_count} total rows, showing up to 20):
{json.dumps(preview_rows, default=str, indent=2)}

Synthesized Natural Language Answer:"""

    response = await llm_client.generate(prompt=prompt, system_prompt=system_prompt)
    return response.strip()


async def answer_question(question: str, max_retries: int = 3) -> dict:
    """Step 3: Orchestrator loop managing grounded planning, execution, self-correction, and synthesis."""
    # Retrieve relevant schema and few-shot golden queries for grounding
    schema = relevant_schema(question, top_k=4)
    golden_examples = retrieve_golden_queries(question, top_k=2)

    sql_attempts = []
    error_context = None

    for attempt in range(1, max_retries + 1):
        try:
            plan_result = await plan(
                question=question,
                schema=schema,
                few_shot_examples=golden_examples,
                error_context=error_context,
            )
        except Exception as e:
            error_msg = f"Planning failed on attempt {attempt}: {str(e)}"
            sql_attempts.append({
                "attempt": attempt,
                "reasoning_plan": None,
                "sql_query": None,
                "success": False,
                "error": error_msg,
            })
            error_context = error_msg
            continue

        reasoning_plan = plan_result.get("reasoning_plan")
        sql_query = plan_result.get("sql_query")

        exec_result = await execute_sql(sql_query)

        attempt_trace = {
            "attempt": attempt,
            "reasoning_plan": reasoning_plan,
            "sql_query": sql_query,
            "success": exec_result["success"],
            "error": exec_result["error"],
            "row_count": len(exec_result["rows"]),
        }
        sql_attempts.append(attempt_trace)

        if exec_result["success"]:
            answer_text = await synthesize_answer(
                question=question,
                columns=exec_result["columns"],
                rows=exec_result["rows"],
            )
            return {
                "success": True,
                "answer": answer_text,
                "sql_attempts": sql_attempts,
                "final_sql": sql_query,
                "rows": exec_result["rows"],
                "columns": exec_result["columns"],
            }
        else:
            error_context = (
                f"SQL Query attempted:\n{sql_query}\n\n"
                f"Database Error:\n{exec_result['error']}"
            )

    last_error = sql_attempts[-1]["error"] if sql_attempts else "Unknown error occurred."
    fallback_answer = (
        f"I was unable to successfully answer your question after {max_retries} attempts. "
        f"Last encountered error: {last_error}"
    )

    return {
        "success": False,
        "answer": fallback_answer,
        "sql_attempts": sql_attempts,
        "final_sql": sql_attempts[-1].get("sql_query") if sql_attempts else None,
        "rows": [],
        "columns": [],
    }
