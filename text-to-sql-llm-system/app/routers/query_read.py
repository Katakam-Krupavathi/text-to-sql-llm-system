from fastapi import APIRouter
from app.schemas.query_schema import QueryRequest
from app.core.llm_client import generate_sql
from app.core.sql_validator import validate_sql
from app.services.query_executor import execute_query
from app.core.context_store import schema_cache
import app.utils.common as common
from app.core.query_history import query_logs
import app.core.pending_queries as pending

router = APIRouter()
@router.post("/query")
async def query_database(request: QueryRequest):

    if common.current_db is None:   
        return {
            "error": "No database uploaded"
        }

    schema = schema_cache.get("current_schema")

    sql = generate_sql(    # idhi ichina question , schema llm ki ichindhi
        request.question,
        schema
    )

   

    sql = sql.strip()               # query ni clean chesidhi
    sql = sql.replace("```sql", "")
    sql = sql.replace("```", "")
    sql = sql.strip()

    print("Generated SQL:", sql)

  

    if request.mode.upper() == "READ":      # read kosam idhi 

        if not sql.upper().startswith("SELECT"):
            return {
                "error":"Only SELECT allowed in READ mode"
            }

    elif request.mode.upper() == "WRITE":   # write kosam idhi

        allowed = ["INSERT", "UPDATE", "DELETE"]

        first_word = sql.upper().split()[0]

        if first_word not in allowed:
            return {
                "error":"Invalid WRITE query"
            }

    else:

        return {
            "error":"Mode should be READ or WRITE"
        }

    is_safe = validate_sql(sql)

    if not is_safe:
        return {
            "error":"Unsafe query blocked"
        }
    if request.mode.upper() == "WRITE":

        pending.pending_query = sql

        return {
            "message": "Please confirm execution",
            "generated_sql": sql
    }

    result = execute_query(
        common.current_db,
        sql
    )

    query_logs.append(
        {
            "question": request.question,
            "generated_sql": sql,
            "result": result
        }
    )

    return {
        "question": request.question,
        "generated_sql": sql,
        "result": result
    }


@router.post("/confirm")
async def confirm_query():

    import app.core.pending_queries as pending

    if pending.pending_query is None:
        return {
            "error":"No pending query"
        }

    result = execute_query(
        common.current_db,
        pending.pending_query
    )

    pending.pending_query = None

    return {
        "message":"Query executed",
        "result": result
    }