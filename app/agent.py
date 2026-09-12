import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import text
import sqlglot
from sqlglot import exp
from app.audit import audit_logger, calculate_cost, estimate_tokens
from app.config import settings
from app.db import readonly_engine
from app.llm import llm_client, llm_router
from app.memory import memory_store
from app.retrieval import relevant_schema, retrieve_golden_queries, DEFAULT_TABLE_SCHEMAS
from app.validator import validate_and_normalize_sql, validate_write_sql

from app.connections import get_engine_for_connection

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
    conversation_history: Optional[str] = None,
    error_context: Optional[str] = None,
    dialect: Optional[str] = None,
    connection_id: Optional[str] = None,
) -> dict:
    """Step 1: Calls LLM with conversation history, dialect enforcement, schema, and few-shot examples."""
    target_dialect = (dialect or settings.SQL_DIALECT).lower()
    if not schema:
        # Combine question with conversation history for better schema retrieval on follow-ups
        retrieval_query = f"{conversation_history or ''} {question}".strip()
        schema = relevant_schema(retrieval_query, top_k=5, connection_id=connection_id)

    if few_shot_examples is None:
        few_shot_examples = retrieve_golden_queries(question, top_k=2, connection_id=connection_id)

    system_prompt = f"""You are an expert SQL engineer. Your target database dialect is strictly {target_dialect.upper()}.
CRITICAL SAFETY & DIALECT INSTRUCTIONS:
- You MUST produce syntactically valid {target_dialect.upper()} SQL queries.
- Do NOT use constructs from other dialects (e.g., do NOT use SQL Server 'TOP n' or 'DATEDIFF', do NOT use Oracle 'NVL').
- Generate ONLY read-only SELECT or WITH ... SELECT queries.
- NEVER generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, GRANT, REVOKE, or administrative commands.
- If conversation history is provided, resolve follow-up references (e.g. 'that', 'those customers', 'now filter by...') by building upon the previous turn's intent and SQL query.
- Return ONLY a valid JSON object with the exact keys: "reasoning_plan", "sql_dialect", and "sql_query".

Output Format JSON:
{{
  "reasoning_plan": "Short chain-of-thought planning which tables, joins, filters, and aggregations to use.",
  "sql_dialect": "{target_dialect}",
  "sql_query": "SELECT ...;"
}}
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

    history_block = conversation_history or ""

    prompt = f"""{examples_block}{history_block}Relevant Database Schema & Sample Values:
{schema}

User Question:
{question}
"""
    if error_context:
        prompt += f"""
Previous Attempt Failed:
{error_context}

Please review the error carefully, diagnose what went wrong in your previous SQL or dialect syntax, and produce a corrected plan and SQL query.
"""

    raw_response = await llm_client.generate(prompt=prompt, system_prompt=system_prompt)
    data = clean_json_response(raw_response)

    if "sql_query" not in data or "reasoning_plan" not in data:
        raise ValueError(f"Model output missing required fields: {data}")

    data.setdefault("sql_dialect", target_dialect)
    return data


def classify_intent(question: str) -> str:
    """
    Lightweight heuristic to detect whether a question intends
    a mutating write operation (INSERT/UPDATE/DELETE) or a read query (SELECT).
    """
    q_lower = question.lower().strip()
    write_patterns = [
        r"\binsert\b",
        r"\badd\s+(?:a\s+|new\s+)?(?:row|record|customer|order|item|user|product)",
        r"\bcreate\s+(?:a\s+|new\s+)?(?:record|row|entry|customer|order|item)",
        r"\bupdate\b",
        r"\bset\b.*\bwhere\b",
        r"\bmodify\b",
        r"\bchange\s+the\b",
        r"\bdelete\b",
        r"\bremove\b",
    ]
    for pattern in write_patterns:
        if re.search(pattern, q_lower):
            return "write"
    return "read"


async def plan_write(
    question: str,
    schema: Optional[str] = None,
    few_shot_examples: Optional[List[dict]] = None,
    conversation_history: Optional[str] = None,
    error_context: Optional[str] = None,
    dialect: Optional[str] = None,
    connection_id: Optional[str] = None,
) -> dict:
    """Step 1 (Write Path): Calls LLM with schema and strict write constraints (INSERT/UPDATE/DELETE with WHERE)."""
    target_dialect = (dialect or settings.SQL_DIALECT).lower()
    if not schema:
        retrieval_query = f"{conversation_history or ''} {question}".strip()
        schema = relevant_schema(retrieval_query, top_k=5, connection_id=connection_id)

    system_prompt = f"""You are an expert SQL database engineer. Your target database dialect is strictly {target_dialect.upper()}.
CRITICAL SAFETY & WRITE INSTRUCTIONS:
- You MUST produce syntactically valid {target_dialect.upper()} data modification SQL queries.
- Generate ONLY INSERT, UPDATE, or DELETE statements.
- For UPDATE and DELETE queries, you MUST include a specific WHERE clause. Unconditional mass updates or deletes (without WHERE) are strictly prohibited.
- NEVER generate DROP, ALTER, TRUNCATE, CREATE TABLE, GRANT, REVOKE, or multi-statement queries.
- Return ONLY a valid JSON object with the exact keys: "reasoning_plan", "sql_dialect", and "sql_query".

Output Format JSON:
{{
  "reasoning_plan": "Short chain-of-thought explaining the target table, affected fields, and WHERE conditions.",
  "sql_dialect": "{target_dialect}",
  "sql_query": "UPDATE ... SET ... WHERE ...;"
}}
"""

    history_block = conversation_history or ""
    prompt = f"""{history_block}Relevant Database Schema & Sample Values:
{schema}

User Request (Data Modification):
{question}
"""
    if error_context:
        prompt += f"""
Previous Attempt Failed:
{error_context}

Please review the error carefully and produce a corrected mutating SQL query.
"""

    raw_response = await llm_client.generate(prompt=prompt, system_prompt=system_prompt)
    data = clean_json_response(raw_response)

    if "sql_query" not in data or "reasoning_plan" not in data:
        raise ValueError(f"Model output missing required fields: {data}")

    data.setdefault("sql_dialect", target_dialect)
    return data


async def generate_write_preview(
    sql_query: str,
    engine: Any,
    dialect: Optional[str] = None,
    max_preview_rows: int = 100,
) -> Tuple[str, List[Dict[str, Any]], int]:
    """
    Generates a dry-run preview for an INSERT/UPDATE/DELETE statement without mutating data.
    - For UPDATE/DELETE: runs a read-only SELECT * FROM table WHERE ... LIMIT 100 on the engine.
    - For INSERT: extracts parsed rows/values to be inserted.
    Returns: (operation_name, preview_rows, affected_count_estimate)
    """
    target_dialect = (dialect or settings.SQL_DIALECT).lower()
    is_valid, normalized_sql, err = validate_write_sql(sql_query, target_dialect=target_dialect)
    if not is_valid:
        raise ValueError(err)

    glot_dialect = "postgres" if target_dialect in ("postgres", "postgresql") else target_dialect
    parsed = sqlglot.parse_one(normalized_sql, read=glot_dialect)

    if isinstance(parsed, exp.Insert):
        operation = "INSERT"
        table_name = parsed.this.this.sql(dialect=glot_dialect) if hasattr(parsed.this, "this") else parsed.this.sql(dialect=glot_dialect)
        
        col_names = []
        if isinstance(parsed.this, exp.Schema) and parsed.this.expressions:
            col_names = [col.sql(dialect=glot_dialect).strip('"') for col in parsed.this.expressions]

        preview_rows = []
        values_expr = parsed.find(exp.Values)
        if values_expr and values_expr.expressions:
            for tuple_expr in values_expr.expressions:
                if isinstance(tuple_expr, exp.Tuple):
                    row_vals = [val.sql(dialect=glot_dialect).strip("'") for val in tuple_expr.expressions]
                else:
                    row_vals = [tuple_expr.sql(dialect=glot_dialect).strip("'")]
                
                if col_names and len(col_names) == len(row_vals):
                    row_dict = dict(zip(col_names, row_vals))
                else:
                    row_dict = {col_names[i] if i < len(col_names) else f"col_{i+1}": v for i, v in enumerate(row_vals)}
                row_dict["_table"] = table_name
                preview_rows.append(row_dict)
        elif not preview_rows:
            preview_rows = [{"_operation": "INSERT", "_table": table_name, "_query": normalized_sql}]

        return operation, preview_rows, len(preview_rows)

    elif isinstance(parsed, (exp.Update, exp.Delete)):
        operation = "UPDATE" if isinstance(parsed, exp.Update) else "DELETE"
        table_name = parsed.this.sql(dialect=glot_dialect)
        where_node = parsed.args.get("where") or parsed.find(exp.Where)
        if not where_node or not where_node.this:
            raise ValueError(f"Cannot preview {operation} without a WHERE clause.")

        where_sql = where_node.sql(dialect=glot_dialect)
        preview_select = f"SELECT * FROM {table_name} {where_sql} LIMIT {max_preview_rows}"
        
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text(preview_select))
                cols = list(result.keys()) if result.returns_rows else []
                raw_rows = result.fetchmany(max_preview_rows) if result.returns_rows else []
                preview_rows = [dict(zip(cols, row)) for row in raw_rows]
                return operation, preview_rows, len(preview_rows)
        except Exception as e:
            logger.warning(f"Dry-run preview SELECT failed: {e}")
            return operation, [], 0
    else:
        raise ValueError(f"Unsupported write operation: {type(parsed).__name__}")


def validate_is_select_query(query: str, dialect: Optional[str] = None) -> None:
    """AST validator checking that the query is strictly a read-only SELECT statement."""
    target_dialect = (dialect or settings.SQL_DIALECT).lower()
    is_valid, _, error_msg = validate_and_normalize_sql(query, target_dialect=target_dialect)
    if not is_valid:
        raise ValueError(error_msg)


async def execute_sql(
    query: str,
    engine: Optional[Any] = None,
    dialect: Optional[str] = None,
    max_rows: int = settings.MAX_QUERY_ROWS,
    timeout_seconds: int = settings.QUERY_TIMEOUT_SECONDS,
) -> dict:
    """Step 2: Validates AST with sqlglot and runs query against the target DB connection."""
    target_dialect = (dialect or settings.SQL_DIALECT).lower()
    is_valid, normalized_sql, validation_error = validate_and_normalize_sql(query, target_dialect=target_dialect)
    if not is_valid:
        return {
            "success": False,
            "rows": [],
            "columns": [],
            "error": validation_error,
            "ast_valid": False,
            "dialect_valid": False,
        }

    target_engine = engine or readonly_engine

    try:
        async with target_engine.connect() as conn:
            if "postgres" in target_dialect:
                try:
                    await conn.execute(text(f"SET LOCAL statement_timeout = {timeout_seconds * 1000}"))
                except Exception:
                    pass

            result = await conn.execute(text(normalized_sql))
            columns = list(result.keys()) if result.returns_rows else []
            raw_rows = result.fetchmany(max_rows) if result.returns_rows else []

            rows = [dict(zip(columns, row)) for row in raw_rows]

            return {
                "success": True,
                "rows": rows,
                "columns": columns,
                "error": None,
                "ast_valid": True,
                "dialect_valid": True,
                "normalized_sql": normalized_sql,
            }
    except Exception as e:
        logger.warning(f"SQL execution error for query '{query}': {e}")
        return {
            "success": False,
            "rows": [],
            "columns": [],
            "error": str(e),
            "ast_valid": True,
            "dialect_valid": True,
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


async def answer_question(
    question: str,
    session_id: Optional[str] = None,
    connection_id: Optional[str] = None,
    connection_string: Optional[str] = None,
    dialect: Optional[str] = None,
    max_retries: int = settings.MAX_RETRIES,
) -> dict:
    """Step 3: Orchestrator loop managing memory, planning, AST validation, execution, self-correction, and audit."""
    start_total_time = time.time()
    target_dialect = (dialect or settings.SQL_DIALECT).lower()
    effective_session_id = memory_store.get_or_create_session_id(session_id)
    history_context = memory_store.format_history_for_prompt(effective_session_id, limit=2)

    # Resolve target DB engine
    if connection_id and connection_string:
        exec_engine = get_engine_for_connection(connection_id, connection_string)
    else:
        exec_engine = readonly_engine

    # Grounding retrieval (incorporating history context and connection namespace)
    retrieval_prompt = f"{history_context} {question}".strip()
    schema = relevant_schema(retrieval_prompt, top_k=4, connection_id=connection_id)
    golden_examples = retrieve_golden_queries(question, top_k=2, connection_id=connection_id)

    sql_attempts = []
    error_context = None
    total_tokens_used = 0
    total_cost = 0.0

    for attempt in range(1, max_retries + 1):
        attempt_start = time.time()
        try:
            plan_result = await plan(
                question=question,
                schema=schema,
                few_shot_examples=golden_examples,
                conversation_history=history_context,
                error_context=error_context,
                dialect=target_dialect,
                connection_id=connection_id,
            )
        except Exception as e:
            error_msg = f"Planning failed on attempt {attempt}: {str(e)}"
            latency_ms = (time.time() - attempt_start) * 1000.0
            sql_attempts.append({
                "attempt": attempt,
                "reasoning_plan": None,
                "sql_query": None,
                "success": False,
                "error": error_msg,
            })
            audit_logger.log_attempt(
                question=question,
                attempt=attempt,
                reasoning_plan=None,
                sql_query=None,
                sql_dialect=target_dialect,
                dialect_valid=False,
                ast_valid=False,
                execution_success=False,
                error_message=error_msg,
                latency_ms=latency_ms,
            )
            error_context = error_msg
            continue

        reasoning_plan = plan_result.get("reasoning_plan")
        sql_query = plan_result.get("sql_query")

        prompt_tokens = estimate_tokens(question + schema + (history_context or ""))
        comp_tokens = estimate_tokens(reasoning_plan + (sql_query or ""))
        step_tokens = prompt_tokens + comp_tokens
        step_cost = calculate_cost(prompt_tokens, comp_tokens)
        total_tokens_used += step_tokens
        total_cost += step_cost

        exec_result = await execute_sql(
            sql_query,
            engine=exec_engine,
            dialect=target_dialect,
        )
        latency_ms = (time.time() - attempt_start) * 1000.0

        attempt_trace = {
            "attempt": attempt,
            "reasoning_plan": reasoning_plan,
            "sql_query": sql_query,
            "success": exec_result["success"],
            "error": exec_result["error"],
            "row_count": len(exec_result["rows"]),
            "ast_valid": exec_result.get("ast_valid", False),
            "dialect_valid": exec_result.get("dialect_valid", False),
            "latency_ms": round(latency_ms, 2),
        }
        sql_attempts.append(attempt_trace)

        audit_logger.log_attempt(
            question=question,
            attempt=attempt,
            reasoning_plan=reasoning_plan,
            sql_query=sql_query,
            sql_dialect=target_dialect,
            dialect_valid=exec_result.get("dialect_valid", False),
            ast_valid=exec_result.get("ast_valid", False),
            execution_success=exec_result["success"],
            error_message=exec_result["error"],
            latency_ms=latency_ms,
            tokens_used=step_tokens,
            cost_usd=step_cost,
        )

        if exec_result["success"]:
            final_sql = exec_result.get("normalized_sql", sql_query)
            answer_text = await synthesize_answer(
                question=question,
                columns=exec_result["columns"],
                rows=exec_result["rows"],
            )
            synth_tokens = estimate_tokens(answer_text)
            total_tokens_used += synth_tokens
            total_cost += calculate_cost(estimate_tokens(question), synth_tokens)

            # Store completed turn into conversation memory
            memory_store.add_turn(
                session_id=effective_session_id,
                question=question,
                reasoning_plan=reasoning_plan,
                sql_query=final_sql,
                answer=answer_text,
            )

            return {
                "success": True,
                "session_id": effective_session_id,
                "answer": answer_text,
                "sql_attempts": sql_attempts,
                "final_sql": final_sql,
                "rows": exec_result["rows"],
                "columns": exec_result["columns"],
                "total_tokens_used": total_tokens_used,
                "estimated_cost_usd": round(total_cost, 6),
                "total_latency_ms": round((time.time() - start_total_time) * 1000.0, 2),
            }
        else:
            error_context = (
                f"SQL Query attempted:\n{sql_query}\n\n"
                f"Validation/Database Error:\n{exec_result['error']}"
            )

    last_error = sql_attempts[-1]["error"] if sql_attempts else "Unknown error occurred."
    fallback_answer = (
        f"I was unable to successfully answer your question after {max_retries} attempts. "
        f"Last encountered error: {last_error}"
    )

    return {
        "success": False,
        "session_id": effective_session_id,
        "answer": fallback_answer,
        "sql_attempts": sql_attempts,
        "final_sql": sql_attempts[-1].get("sql_query") if sql_attempts else None,
        "rows": [],
        "columns": [],
        "total_tokens_used": total_tokens_used,
        "estimated_cost_usd": round(total_cost, 6),
        "total_latency_ms": round((time.time() - start_total_time) * 1000.0, 2),
    }
