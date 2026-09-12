from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.db import check_db_connection, main_engine, readonly_engine

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
    version="0.1.0",
    description="Production-ready Text-to-SQL Agent with Schema-Linking, Guardrails, and Self-Correction",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "service": settings.APP_NAME,
        "status": "online",
        "sql_dialect": settings.SQL_DIALECT,
        "version": "0.1.0",
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
    }
