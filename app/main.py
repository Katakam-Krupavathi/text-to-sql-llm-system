from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any, Dict, List, Optional
import uuid
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.agent import answer_question, generate_write_preview, plan_write
from app.audit import audit_logger
from app.auth import create_access_token, get_current_user, hash_password, verify_password, security_bearer, decode_access_token
from app.config import settings, validate_security_keys
from app.connections import (
    close_engine_for_connection,
    get_engine_for_connection,
    test_and_probe_connection,
)
from app.crypto import decrypt_connection_string, encrypt_connection_string
from app.db import check_db_connection, main_engine, readonly_engine
from app.memory import memory_store
from app.models import (
    create_database_connection,
    create_user,
    delete_database_connection,
    get_connection_by_id,
    get_connections_for_user,
    get_user_by_email,
    get_user_by_id,
)
from app.rate_limiter import rate_limit_dependency
from app.retrieval import delete_connection_retrieval_index, get_retrieval_index
from app.validator import validate_write_sql
from fastapi.security import HTTPAuthorizationCredentials

logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_security_keys(settings)
    logger.info(f"Starting {settings.APP_NAME} (Dialect: {settings.SQL_DIALECT})")
    yield
    logger.info("Shutting down database engines...")
    await main_engine.dispose()
    await readonly_engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.5.0",
    description="Production-ready Text-to-SQL Agent with Multi-Tenant Auth, BYODB Support, Opt-In Safe Write Flow, Conversation Memory, AST Validation, Dialect Enforcement, and Guardrails",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request & Response Schemas ---

class RegisterRequest(BaseModel):
    email: str = Field(..., description="User email address")
    password: str = Field(..., min_length=6, description="User password (min 6 characters)")


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


class UserResponse(BaseModel):
    id: str
    email: str
    created_at: str


class CreateConnectionRequest(BaseModel):
    nickname: str = Field(..., min_length=1, max_length=100, description="Friendly nickname for the DB")
    dialect: str = Field(default="postgres", description="Database dialect (e.g. postgres, sqlite, mysql)")
    connection_string: str = Field(..., min_length=1, description="Database connection URI")
    allow_writes: bool = Field(default=False, description="Opt-in flag enabling INSERT/UPDATE/DELETE write operations on this connection")


class ConnectionResponse(BaseModel):
    id: str
    user_id: str
    nickname: str
    dialect: str
    is_read_only: bool
    allow_writes: bool = False
    created_at: str
    last_validated_at: str
    warning: Optional[str] = None


class AskRequest(BaseModel):
    question: str = Field(..., description="Natural language question to ask the database", min_length=1)
    connection_id: Optional[str] = Field(default=None, description="Optional target BYODB connection ID")
    session_id: Optional[str] = Field(default=None, description="Optional session ID for multi-turn conversation memory")
    max_retries: Optional[int] = Field(default=3, ge=1, le=5)


class AskResponse(BaseModel):
    session_id: str
    answer: str
    sql_attempts: List[Dict[str, Any]]
    final_sql: Optional[str]
    rows: List[Dict[str, Any]]
    columns: List[str]
    total_tokens_used: Optional[int] = 0
    estimated_cost_usd: Optional[float] = 0.0
    total_latency_ms: Optional[float] = 0.0


class AskWriteRequest(BaseModel):
    question: str = Field(..., description="Natural language request requesting a data modification (INSERT/UPDATE/DELETE)", min_length=1)
    connection_id: Optional[str] = Field(default=None, description="Optional target database connection ID")
    session_id: Optional[str] = Field(default=None, description="Optional conversation session ID")


class AskWriteResponse(BaseModel):
    preview_token: str = Field(..., description="Short-lived (5m) cryptographically signed token bound to the exact SQL previewed")
    operation: str = Field(..., description="Mutating operation type (INSERT, UPDATE, DELETE)")
    sql_query: str = Field(..., description="Generated and validated SQL query")
    reasoning_plan: str = Field(..., description="Agent chain-of-thought explanation")
    preview_rows: List[Dict[str, Any]] = Field(default_factory=list, description="Affected rows preview for UPDATE/DELETE or formatted records for INSERT")
    affected_count_estimate: int = Field(default=0, description="Estimated number of affected rows")
    expires_in_seconds: int = Field(default=300, description="Token expiration window in seconds")


class ConfirmWriteRequest(BaseModel):
    preview_token: str = Field(..., description="Preview token received from POST /ask/write")


class ConfirmWriteResponse(BaseModel):
    status: str = "committed"
    sql: str
    affected_rows: int
    timestamp: str


async def get_optional_current_user(
    auth: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
) -> Optional[dict]:
    """Resolves current user if Bearer token is present, otherwise returns None."""
    if not auth or not auth.credentials:
        return None
    try:
        payload = decode_access_token(auth.credentials)
        user_id = payload.get("sub")
        if user_id:
            return get_user_by_id(user_id)
    except Exception:
        pass
    return None


# --- System & Health Endpoints ---

@app.get("/")
async def root():
    return {
        "service": settings.APP_NAME,
        "status": "online",
        "sql_dialect": settings.SQL_DIALECT,
        "version": "0.4.0",
        "rate_limit_per_minute": settings.RATE_LIMIT_PER_MINUTE,
    }


@app.get("/health")
async def health_check():
    db_health = await check_db_connection()
    return {
        "status": "healthy" if db_health.get("connected") else "degraded",
        "dialect": settings.SQL_DIALECT,
        "database": db_health,
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL,
        "rate_limit_per_minute": settings.RATE_LIMIT_PER_MINUTE,
    }


# --- Auth Endpoints ---

@app.post("/auth/register", response_model=TokenResponse)
async def register(req: RegisterRequest):
    """Registers a new user account and returns a JWT access token."""
    existing = get_user_by_email(req.email)
    if existing:
        raise HTTPException(status_code=400, detail="User with this email already exists.")
    
    hashed = hash_password(req.password)
    user = create_user(email=req.email, hashed_password=hashed)
    token = create_access_token({"sub": user["id"], "email": user["email"]})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user["id"],
        email=user["email"],
    )


@app.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """Authenticates a user and returns a JWT access token."""
    user = get_user_by_email(req.email)
    if not user or not verify_password(req.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    
    token = create_access_token({"sub": user["id"], "email": user["email"]})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user["id"],
        email=user["email"],
    )


@app.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Returns the profile of the currently authenticated user."""
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        created_at=current_user["created_at"],
    )


# --- Database Connection Management (BYODB) Endpoints ---

@app.post("/connections", response_model=ConnectionResponse)
async def create_connection(
    req: CreateConnectionRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Tests, write-probes, encrypts, and registers a new database connection for the authenticated user.
    """
    # 1. Test connection and probe write permissions
    is_valid, is_read_only, warning_msg, err_msg = await test_and_probe_connection(
        req.connection_string, req.dialect
    )
    if not is_valid:
        raise HTTPException(status_code=400, detail=err_msg or "Failed to connect to database.")

    # 2. Encrypt connection string at rest
    encrypted_str = encrypt_connection_string(req.connection_string)

    # 3. Save connection record in DB
    record = create_database_connection(
        user_id=current_user["id"],
        nickname=req.nickname,
        dialect=req.dialect,
        encrypted_connection_string=encrypted_str,
        is_read_only=is_read_only,
        allow_writes=req.allow_writes,
    )

    # 4. Automatically index schema for this connection
    try:
        engine = get_engine_for_connection(record["id"], req.connection_string)
        idx = get_retrieval_index(record["id"])
        await idx.build_schema_index_from_engine(engine, dialect=req.dialect)
    except Exception as e:
        logger.warning(f"Initial schema indexing failed for connection {record['id']}: {e}")

    return ConnectionResponse(
        id=record["id"],
        user_id=record["user_id"],
        nickname=record["nickname"],
        dialect=record["dialect"],
        is_read_only=record["is_read_only"],
        allow_writes=record.get("allow_writes", False),
        created_at=record["created_at"],
        last_validated_at=record["last_validated_at"],
        warning=warning_msg,
    )


@app.get("/connections", response_model=List[ConnectionResponse])
async def list_connections(current_user: dict = Depends(get_current_user)):
    """Lists all database connections owned by the current user."""
    conns = get_connections_for_user(current_user["id"])
    return [ConnectionResponse(**c) for c in conns]


@app.delete("/connections/{connection_id}")
async def delete_connection(
    connection_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Deletes a database connection and purges its cached schema index."""
    deleted = delete_database_connection(connection_id, current_user["id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Connection not found.")

    await close_engine_for_connection(connection_id)
    delete_connection_retrieval_index(connection_id)
    return {"message": f"Connection {connection_id} deleted successfully."}


# --- Agent Query & Audit Endpoints ---

@app.post("/ask", response_model=AskResponse, dependencies=[Depends(rate_limit_dependency)])
async def ask(
    request: AskRequest,
    current_user: Optional[dict] = Depends(get_optional_current_user),
):
    """Processes a question through the guarded agent loop with multi-tenant BYODB routing."""
    target_conn_str = None
    target_dialect = settings.SQL_DIALECT
    target_conn_id = None

    if request.connection_id:
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required to query custom connection.")
        
        conn_record = get_connection_by_id(request.connection_id)
        if not conn_record or conn_record["user_id"] != current_user["id"]:
            raise HTTPException(status_code=404, detail="Connection not found or access denied.")
        
        target_conn_str = decrypt_connection_string(conn_record["encrypted_connection_string"])
        target_dialect = conn_record["dialect"]
        target_conn_id = conn_record["id"]
    elif current_user:
        # Default to user's first registered connection if available
        user_conns = get_connections_for_user(current_user["id"])
        if user_conns:
            first_conn = get_connection_by_id(user_conns[0]["id"])
            if first_conn:
                target_conn_str = decrypt_connection_string(first_conn["encrypted_connection_string"])
                target_dialect = first_conn["dialect"]
                target_conn_id = first_conn["id"]

    try:
        result = await answer_question(
            question=request.question,
            session_id=request.session_id,
            connection_id=target_conn_id,
            connection_string=target_conn_str,
            dialect=target_dialect,
            max_retries=request.max_retries or settings.MAX_RETRIES,
        )
        return AskResponse(
            session_id=result["session_id"],
            answer=result["answer"],
            sql_attempts=result["sql_attempts"],
            final_sql=result["final_sql"],
            rows=result["rows"],
            columns=result["columns"],
            total_tokens_used=result.get("total_tokens_used", 0),
            estimated_cost_usd=result.get("estimated_cost_usd", 0.0),
            total_latency_ms=result.get("total_latency_ms", 0.0),
        )
    except Exception as e:
        logger.error(f"Unhandled error answering question: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ask/write", response_model=AskWriteResponse, dependencies=[Depends(rate_limit_dependency)])
async def ask_write(
    request: AskWriteRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Generates a mutating SQL query (INSERT/UPDATE/DELETE) via plan_write(), runs a dry-run preview,
    and returns affected rows along with a short-lived preview token.
    Never executes the mutating statement directly.
    """
    target_conn_str = None
    target_dialect = settings.SQL_DIALECT
    target_conn_id = None
    target_engine = readonly_engine

    if request.connection_id:
        conn_record = get_connection_by_id(request.connection_id)
        if not conn_record or conn_record["user_id"] != current_user["id"]:
            raise HTTPException(status_code=404, detail="Connection not found or access denied.")
        if not conn_record.get("allow_writes", False):
            raise HTTPException(
                status_code=403,
                detail="Write operations are disabled for this database connection. Set 'allow_writes: true' on the connection to enable writes.",
            )
        target_conn_str = decrypt_connection_string(conn_record["encrypted_connection_string"])
        target_dialect = conn_record["dialect"]
        target_conn_id = conn_record["id"]
        target_engine = get_engine_for_connection(target_conn_id, target_conn_str)
        db_target = f"connection:{target_conn_id}"
    else:
        user_conns = get_connections_for_user(current_user["id"])
        if user_conns:
            first_conn = get_connection_by_id(user_conns[0]["id"])
            if first_conn:
                if not first_conn.get("allow_writes", False):
                    raise HTTPException(
                        status_code=403,
                        detail="Write operations are disabled for this database connection. Set 'allow_writes: true' on the connection to enable writes.",
                    )
                target_conn_str = decrypt_connection_string(first_conn["encrypted_connection_string"])
                target_dialect = first_conn["dialect"]
                target_conn_id = first_conn["id"]
                target_engine = get_engine_for_connection(target_conn_id, target_conn_str)
                db_target = f"connection:{target_conn_id}"
        else:
            if not settings.ALLOW_DEFAULT_DB_WRITES:
                raise HTTPException(
                    status_code=403,
                    detail="Writes against the default database are disabled. Configure a database connection with allow_writes=true, or set ALLOW_DEFAULT_DB_WRITES=true if you intend to allow writes against the primary application database.",
                )
            target_conn_str = settings.DATABASE_URL
            target_dialect = settings.SQL_DIALECT
            target_conn_id = None
            target_engine = main_engine
            db_target = "default_db"

    # 1. Generate plan for write operation
    try:
        plan_result = await plan_write(
            question=request.question,
            dialect=target_dialect,
            connection_id=target_conn_id,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to plan write SQL: {str(e)}")

    sql_query = plan_result.get("sql_query")
    reasoning_plan = plan_result.get("reasoning_plan", "")

    # 2. Validate write SQL strictly (enforces WHERE clause on UPDATE/DELETE)
    is_valid, normalized_sql, validation_err = validate_write_sql(sql_query, target_dialect=target_dialect)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Write SQL validation failed: {validation_err}")

    # 3. Generate dry-run preview (runs SELECT or extracts insert row)
    try:
        operation, preview_rows, affected_count = await generate_write_preview(
            normalized_sql,
            engine=target_engine,
            dialect=target_dialect,
            max_preview_rows=100,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Preview generation failed: {str(e)}")

    # 4. Generate 5-minute cryptographically signed preview token
    token_payload = {
        "jti": str(uuid.uuid4()),
        "sub": current_user["id"],
        "connection_id": target_conn_id,
        "db_target": db_target,
        "sql": normalized_sql,
        "operation": operation,
        "type": "write_preview",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    preview_token = jwt.encode(token_payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    return AskWriteResponse(
        preview_token=preview_token,
        operation=operation,
        sql_query=normalized_sql,
        reasoning_plan=reasoning_plan,
        preview_rows=preview_rows,
        affected_count_estimate=affected_count,
        expires_in_seconds=300,
    )


@app.post("/ask/write/confirm", response_model=ConfirmWriteResponse)
async def confirm_write(
    request: ConfirmWriteRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Explicitly executes and commits a previously previewed write query inside a database transaction.
    Re-validates preview token, connection write permission, and SQL safety before executing.
    """
    # 1. Validate preview token
    try:
        payload = jwt.decode(request.preview_token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "write_preview":
            raise HTTPException(status_code=400, detail="Invalid token type.")
        if payload.get("sub") != current_user["id"]:
            raise HTTPException(status_code=403, detail="Token does not belong to the authenticated user.")
    except JWTError as e:
        raise HTTPException(status_code=400, detail=f"Invalid or expired preview token: {e}")

    sql_to_execute = payload.get("sql")
    target_conn_id = payload.get("connection_id")
    token_db_target = payload.get("db_target")
    jti = payload.get("jti") or str(uuid.uuid4())

    # Check and enforce single-use token consumption
    if not audit_logger.consume_preview_token(jti, current_user["id"], sql_to_execute):
        raise HTTPException(
            status_code=409,
            detail="This write has already been executed or is being processed.",
        )

    # 2. Resolve write-capable database connection
    if target_conn_id:
        conn_record = get_connection_by_id(target_conn_id)
        if not conn_record or conn_record["user_id"] != current_user["id"]:
            raise HTTPException(status_code=404, detail="Target connection not found or access denied.")
        if not conn_record.get("allow_writes", False):
            raise HTTPException(status_code=403, detail="Write operations are disabled for this database connection.")
        target_conn_str = decrypt_connection_string(conn_record["encrypted_connection_string"])
        target_dialect = conn_record["dialect"]
        write_engine = get_engine_for_connection(target_conn_id, target_conn_str)
        derived_db_target = f"connection:{target_conn_id}"
    else:
        if not settings.ALLOW_DEFAULT_DB_WRITES:
            raise HTTPException(
                status_code=403,
                detail="Writes against the default database are disabled. Configure a database connection with allow_writes=true, or set ALLOW_DEFAULT_DB_WRITES=true if you intend to allow writes against the primary application database.",
            )
        target_dialect = settings.SQL_DIALECT
        write_engine = main_engine
        derived_db_target = "default_db"

    # Verify db_target matches preview
    if token_db_target and token_db_target != derived_db_target:
        raise HTTPException(
            status_code=400,
            detail=f"Database target mismatch: token previewed for '{token_db_target}' but execution target resolved to '{derived_db_target}'.",
        )

    # 3. Re-validate write SQL before execution (never trust token blindly)
    is_valid, normalized_sql, validation_err = validate_write_sql(sql_to_execute, target_dialect=target_dialect)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Security re-validation failed: {validation_err}")

    # 4. Execute inside a transactional block (commits on clean exit, rolls back on exception)
    start_t = time.time()
    try:
        async with write_engine.begin() as conn:
            result = await conn.execute(text(normalized_sql))
            affected_rows = result.rowcount if hasattr(result, "rowcount") and result.rowcount is not None and result.rowcount >= 0 else 1

        latency_ms = (time.time() - start_t) * 1000.0
        audit_logger.log_write_execution(
            user_id=current_user["id"],
            sql_query=normalized_sql,
            sql_dialect=target_dialect,
            affected_rows=affected_rows,
            execution_success=True,
            latency_ms=latency_ms,
        )

        return ConfirmWriteResponse(
            status="committed",
            sql=normalized_sql,
            affected_rows=affected_rows,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        latency_ms = (time.time() - start_t) * 1000.0
        err_msg = str(e)
        logger.error(f"Write transaction failed for query '{normalized_sql}': {err_msg}")
        audit_logger.log_write_execution(
            user_id=current_user["id"],
            sql_query=normalized_sql,
            sql_dialect=target_dialect,
            affected_rows=0,
            execution_success=False,
            error_message=err_msg,
            latency_ms=latency_ms,
        )
        raise HTTPException(status_code=500, detail=f"Transaction rolled back due to execution error: {err_msg}")


@app.get("/audit")
async def get_audit_logs(limit: int = 50):
    """Fetches recent audit log records for compliance review."""
    return {"logs": audit_logger.get_recent_logs(limit=limit)}


@app.delete("/sessions/{session_id}")
async def clear_session(session_id: str):
    """Clears conversation memory for a specific session."""
    memory_store.clear_session(session_id)
    return {"message": f"Session {session_id} memory cleared."}

