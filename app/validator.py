import logging
import re
from typing import Tuple
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from app.config import settings

logger = logging.getLogger(__name__)

# List of AST node types that mutate state or manage schema/privileges
FORBIDDEN_EXPRESSION_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Command,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
    exp.Revoke,
    exp.Pragma,
    exp.Kill,
)

# Known dangerous database administration/system functions
FORBIDDEN_FUNCTIONS = {
    "pg_sleep",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_read_file",
    "pg_write_file",
    "dblink",
    "xp_cmdshell",
    "eval",
    "system",
}


class SQLSecurityError(ValueError):
    """Raised when a SQL query violates security or read-only constraints."""
    pass


class SQLDialectError(ValueError):
    """Raised when a SQL query violates target dialect syntax constraints."""
    pass


def validate_and_normalize_sql(
    query: str,
    target_dialect: str = settings.SQL_DIALECT,
) -> Tuple[bool, str, str]:
    """
    Parses and validates a SQL query against security guardrails and dialect compliance.
    Returns: (is_valid, normalized_sql_or_empty, error_message_or_empty)
    """
    if not query or not query.strip():
        return False, "", "Empty query provided."

    cleaned_query = query.strip()
    # Remove leading SQL comments
    cleaned_query = re.sub(r"^--.*$", "", cleaned_query, flags=re.MULTILINE).strip()
    cleaned_query = re.sub(r"^/\*.*?\*/", "", cleaned_query, flags=re.DOTALL).strip()

    # Normalize dialect name for sqlglot (postgres -> postgres)
    glot_dialect = "postgres" if target_dialect.lower() in ("postgres", "postgresql") else target_dialect.lower()

    # 1. Dialect & syntax parsing
    try:
        parsed_statements = sqlglot.parse(cleaned_query, read=glot_dialect)
    except SqlglotError as e:
        return False, "", f"Dialect syntax error ({target_dialect}): {str(e)}"
    except Exception as e:
        return False, "", f"SQL parse failure: {str(e)}"

    if not parsed_statements:
        return False, "", "No executable SQL statements found in query."

    if len(parsed_statements) > 1:
        return False, "", "Multiple SQL statements in a single request are strictly prohibited."

    root_ast = parsed_statements[0]
    if root_ast is None:
        return False, "", "Failed to parse SQL AST."

    # 2. Check for forbidden expression types anywhere in the AST tree
    for node in root_ast.walk():
        if isinstance(node, FORBIDDEN_EXPRESSION_TYPES):
            node_type = type(node).__name__
            return False, "", f"Security Violation: Query contains prohibited operation '{node_type}'."

        # Check for forbidden function calls
        if isinstance(node, exp.Anonymous):
            func_name = node.name.lower()
            if func_name in FORBIDDEN_FUNCTIONS:
                return False, "", f"Security Violation: Query contains forbidden function '{func_name}'."
        elif isinstance(node, exp.Func):
            func_name = node.sql_name().lower() if hasattr(node, "sql_name") else node.key.lower()
            if func_name in FORBIDDEN_FUNCTIONS:
                return False, "", f"Security Violation: Query contains forbidden function '{func_name}'."

    # 3. Verify root AST is a read-only query (Select, Union, or With ... Select)
    # If CTE (With), check the actual query expression being executed
    if isinstance(root_ast, exp.Select) or isinstance(root_ast, exp.Union):
        is_select_query = True
    elif isinstance(root_ast, exp.Expression) and root_ast.find(exp.Select):
        # A CTE with expression containing a select
        is_select_query = True
    else:
        is_select_query = False

    if not is_select_query:
        return False, "", f"Security Violation: Only read-only SELECT queries are permitted (got {type(root_ast).__name__})."

    # 4. Dialect enforcement checks (catch non-postgres constructs like TOP 10, DATEDIFF, etc. if targeting postgres)
    if glot_dialect == "postgres":
        query_upper = cleaned_query.upper()
        # SQL Server 'SELECT TOP' pattern
        if re.search(r"\bSELECT\s+TOP\s+\d+\b", query_upper):
            return False, "", "Dialect error: 'TOP' is not valid PostgreSQL syntax. Use 'LIMIT <n>' instead."
        # SQL Server DATEDIFF
        if re.search(r"\bDATEDIFF\s*\(", query_upper):
            return False, "", "Dialect error: 'DATEDIFF' is not valid PostgreSQL syntax. Use date subtraction or EXTRACT/AGE instead."
        # Oracle NVL
        if re.search(r"\bNVL\s*\(", query_upper):
            return False, "", "Dialect error: 'NVL' is not valid PostgreSQL syntax. Use 'COALESCE' instead."

    # Generate normalized SQL compliant with dialect
    try:
        normalized_sql = root_ast.sql(dialect=glot_dialect)
    except Exception:
        normalized_sql = cleaned_query

    return True, normalized_sql, ""
