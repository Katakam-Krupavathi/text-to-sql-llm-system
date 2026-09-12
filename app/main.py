from contextlib import asynccontextmanager
import logging
from typing import Any, Dict, List, Optional
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.agent import answer_question
from app.audit import audit_logger
from app.auth import create_access_token, get_current_user, hash_password, verify_password, security_bearer, decode_access_token
from app.config import settings
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
from fastapi.security import HTTPAuthorizationCredentials

logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} (Dialect: {settings.SQL_DIALECT})")
    yield
    logger.info("Shutting down database engines...")
    await main_engine.dispose()
    await readonly_engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.4.0",
    description="Production-ready Text-to-SQL Agent with Multi-Tenant Auth, BYODB Support, Conversation Memory, AST Validation, Dialect Enforcement, and Guardrails",
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


class ConnectionResponse(BaseModel):
    id: str
    user_id: str
    nickname: str
    dialect: str
    is_read_only: bool
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


@app.get("/audit")
async def get_audit_logs(limit: int = 50):
    """Fetches recent audit log records for compliance review."""
    return {"logs": audit_logger.get_recent_logs(limit=limit)}


@app.delete("/sessions/{session_id}")
async def clear_session(session_id: str):
    """Clears conversation memory for a specific session."""
    memory_store.clear_session(session_id)
    return {"message": f"Session {session_id} memory cleared."}

