import json
import logging
import re
from typing import Any, Dict, List, Optional
from sqlalchemy import text
from app.config import settings
from app.db import readonly_engine
from app.llm import llm_client

logger = logging.getLogger(__name__)

# Static fallback schema representation based on standard seed database
SAMPLE_SCHEMA = """
Table: categories (
    category_id INTEGER PRIMARY KEY,
    category_name VARCHAR(100),
    description TEXT
)

Table: products (
    product_id INTEGER PRIMARY KEY,
    product_name VARCHAR(150),
    category_id INTEGER REFERENCES categories(category_id),
    unit_price NUMERIC(10, 2),
    units_in_stock INTEGER,
    discontinued BOOLEAN
)

Table: customers (
    customer_id VARCHAR(10) PRIMARY KEY,
    company_name VARCHAR(150),
    contact_name VARCHAR(100),
    country VARCHAR(50),
    city VARCHAR(50)
)

Table: employees (
    employee_id INTEGER PRIMARY KEY,
    first_name VARCHAR(50),
    last_name VARCHAR(50),
    title VARCHAR(100),
    hire_date DATE,
    department VARCHAR(50)
)

Table: orders (
    order_id INTEGER PRIMARY KEY,
    customer_id VARCHAR(10) REFERENCES customers(customer_id),
    employee_id INTEGER REFERENCES employees(employee_id),
    order_date DATE,
    ship_country VARCHAR(50),
    freight NUMERIC(10, 2)
)

Table: order_items (
    order_id INTEGER REFERENCES orders(order_id),
    product_id INTEGER REFERENCES products(product_id),
    unit_price NUMERIC(10, 2),
    quantity INTEGER,
    discount NUMERIC(4, 2),
    PRIMARY KEY (order_id, product_id)
)
"""


async def get_db_schema() -> str:
    """Introspects tables and columns from the live DB, falling back to SAMPLE_SCHEMA if offline."""
    try:
        query = text("""
            SELECT 
                t.table_name, 
                c.column_name, 
                c.data_type
            FROM information_schema.tables t
            JOIN information_schema.columns c ON t.table_name = c.table_name
            WHERE t.table_schema = 'public'
            ORDER BY t.table_name, c.ordinal_position;
        """)
        async with readonly_engine.connect() as conn:
            result = await conn.execute(query)
            rows = result.fetchall()
            if not rows:
                return SAMPLE_SCHEMA

            tables: Dict[str, List[str]] = {}
            for table_name, column_name, data_type in rows:
                tables.setdefault(table_name, []).append(f"    {column_name} {data_type}")

            schema_lines = []
            for t_name, cols in tables.items():
                schema_lines.append(f"Table: {t_name} (\n" + ",\n".join(cols) + "\n)")
            return "\n\n".join(schema_lines)
    except Exception as e:
        logger.debug(f"Dynamic schema extraction failed, using fallback schema: {e}")
        return SAMPLE_SCHEMA


def clean_json_response(raw_text: str) -> dict:
    """Extracts and parses JSON from model output, stripping any markdown backticks."""
    text_content = raw_text.strip()
    if text_content.startswith("```"):
        # Strip markdown code block fences
        text_content = re.sub(r"^```(?:json)?\s*", "", text_content, flags=re.MULTILINE)
        text_content = re.sub(r"\s*```$", "", text_content, flags=re.MULTILINE)
    text_content = text_content.strip()

    # Try direct parsing
    try:
        return json.loads(text_content)
    except json.JSONDecodeError:
        # Fallback: find first { and last }
        start = text_content.find("{")
        end = text_content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text_content[start : end + 1])
        raise ValueError(f"Could not parse valid JSON from LLM output: {raw_text[:200]}")


async def plan(
    question: str,
    schema: str,
    error_context: Optional[str] = None,
) -> dict:
    """Step 1: Calls LLM to produce a structured plan and SQL query."""
    system_prompt = f"""You are an expert SQL engineer. Your target dialect is {settings.SQL_DIALECT.upper()}.
Given a database schema and a natural language question, your job is to:
1. Reason step-by-step in `reasoning_plan` about which tables, joins, columns, filters, and aggregations are required.
2. Produce a valid, syntactically correct {settings.SQL_DIALECT.upper()} SELECT query in `sql_query`.

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

    prompt = f"""Database Schema:
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

    # Validate structure
    if "sql_query" not in data or "reasoning_plan" not in data:
        raise ValueError(f"Model output missing required fields: {data}")

    # Ensure sql_dialect is populated
    data.setdefault("sql_dialect", settings.SQL_DIALECT)
    return data


def validate_is_select_query(query: str) -> None:
    """Ensures the query is strictly a read-only SELECT statement."""
    cleaned = query.strip()
    # Remove leading SQL comments
    cleaned = re.sub(r"^--.*$", "", cleaned, flags=re.MULTILINE).strip()
    cleaned = re.sub(r"^/\*.*?\*/", "", cleaned, flags=re.DOTALL).strip()

    # Disallow multiple statements separated by semicolon (to avoid injection)
    statements = [s.strip() for s in cleaned.split(";") if s.strip()]
    if len(statements) > 1:
        raise ValueError("Multiple SQL statements in a single execution are prohibited.")

    first_stmt = statements[0] if statements else ""
    first_token = first_stmt.split()[0].upper() if first_stmt.split() else ""

    if first_token not in ("SELECT", "WITH"):
        raise ValueError(
            f"Only read-only SELECT queries are allowed. Forbidden statement type: {first_token}"
        )

    # Disallow destructive keywords in the body
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
            # Enforce statement timeout in PostgreSQL if supported
            try:
                await conn.execute(text(f"SET LOCAL statement_timeout = {timeout_seconds * 1000}"))
            except Exception:
                pass  # If engine/dialect doesn't support statement_timeout

            result = await conn.execute(text(query))
            columns = list(result.keys()) if result.returns_rows else []
            raw_rows = result.fetchmany(max_rows) if result.returns_rows else []

            # Format rows as list of dicts for JSON serialization
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
    # Sample up to top 20 rows to avoid blowing context window
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
    """Step 3: Orchestrator loop managing planning, execution, self-correction, and synthesis."""
    schema = await get_db_schema()
    sql_attempts = []
    error_context = None

    for attempt in range(1, max_retries + 1):
        try:
            plan_result = await plan(
                question=question,
                schema=schema,
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

        # Execute query
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
            # Query succeeded! Synthesize final answer
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
            # Prepare error context for next iteration
            error_context = (
                f"SQL Query attempted:\n{sql_query}\n\n"
                f"Database Error:\n{exec_result['error']}"
            )

    # If loop exhausted all retries
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
