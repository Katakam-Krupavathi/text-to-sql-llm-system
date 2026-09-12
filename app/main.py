from contextlib import asynccontextmanager
import logging
from typing import Any, Dict, List, Optional
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.agent import answer_question
from app.audit import audit_logger
from app.config import settings
from app.db import check_db_connection, main_engine, readonly_engine
from app.memory import memory_store
from app.rate_limiter import rate_limit_dependency

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
    version="0.3.0",
    description="Production-ready Text-to-SQL Agent with Conversation Memory, AST Validation, Dialect Enforcement, and Guardrails",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str = Field(..., description="Natural language question to ask the database", min_length=1)
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


@app.get("/")
async def root():
    return {
        "service": settings.APP_NAME,
        "status": "online",
        "sql_dialect": settings.SQL_DIALECT,
        "version": "0.3.0",
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


@app.post("/ask", response_model=AskResponse, dependencies=[Depends(rate_limit_dependency)])
async def ask(request: AskRequest):
    """Processes a question through the guarded agent loop with multi-turn memory and rate limiting."""
    try:
        result = await answer_question(
            question=request.question,
            session_id=request.session_id,
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
