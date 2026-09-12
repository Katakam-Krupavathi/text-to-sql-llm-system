# Text-to-SQL Agent

A production-ready Text-to-SQL system featuring schema-linking retrieval, strict SQL dialect enforcement, safety guardrails, read-only database connections, and self-correction loops.

---

## 🏗️ Project Structure (Phase 0: Skeleton & DB Setup)

```text
.
├── app/
│   ├── __init__.py
│   ├── main.py                # FastAPI entrypoint, CORS, lifespan & health check
│   ├── config.py              # Pydantic Settings (DB URLs, SQL Dialect, LLM config)
│   ├── db.py                  # SQLAlchemy async connection pooling & read-only engine
│   └── llm.py                 # Async LLM API client wrapper (OpenAI & Anthropic)
├── scripts/
│   ├── __init__.py
│   ├── setup_readonly_role.py # Creates a restricted read-only PostgreSQL role
│   └── seed_sample_db.py      # Seeds sample schema and data (Northwind/E-commerce)
├── tests/
│   ├── __init__.py
│   └── test_health.py         # Health check and endpoint smoke tests
├── .env.example               # Template environment configuration
├── .gitignore                 # Comprehensive ignore rules
├── requirements.txt           # Project dependencies
└── README.md                  # Living documentation
```

---

## 🚀 Quickstart & Setup

### 1. Prerequisites
- Python 3.10+
- PostgreSQL instance running locally or via Docker

### 2. Environment Setup

Clone and install dependencies:
```bash
python -m venv env
source env/bin/activate  # On Windows: env\Scripts\activate
pip install -r requirements.txt
```

Create your `.env` configuration:
```bash
cp .env.example .env
```

Edit `.env` to configure your database connection and LLM API keys:
- `DATABASE_URL`: Main PostgreSQL async connection string (`postgresql+asyncpg://...`)
- `READONLY_DATABASE_URL`: Restricted read-only user connection string
- `SQL_DIALECT`: Explicit dialect target (default: `postgres`)
- `LLM_PROVIDER`: `openai` or `anthropic`
- `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`: Corresponding provider API key

### 3. Database Initialization & Read-Only Role

Seed the sample relational schema and data:
```bash
python scripts/seed_sample_db.py --dbname text_to_sql_db --user postgres --password postgres
```

Create a restricted `sql_readonly` user with database-level read-only permissions:
```bash
python scripts/setup_readonly_role.py --dbname text_to_sql_db --admin-user postgres --admin-password postgres
```

### 4. Running the Application

Start the FastAPI development server:
```bash
uvicorn app.main:app --reload --port 8000
```

Access the interactive API documentation at:
- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health Check: [http://localhost:8000/health](http://localhost:8000/health)

---

## 🧠 Core 4-Step Agentic Pipeline & 3-Layer Grounding

### 🛡️ Three Layers of Anti-Hallucination Grounding
To prevent hallucinations across table names, column names, and database values, the agent uses three grounded retrieval layers:

1. **Schema-Linking (Structural Retrieval)**:
   - Tables, columns, and relations are vectorized. At query time, `relevant_schema(question, top_k=4)` selectively extracts and provides only the relevant schema definitions instead of dumping hundreds of irrelevant columns into prompt context.
2. **Value Hinting (Column Data Profiling)**:
   - Low-cardinality columns (e.g. status fields, countries, categories, flags) are profiled at index time. Sample distinct values are appended directly into schema hints (e.g. `discontinued BOOLEAN (sample values: false, true)` or `country VARCHAR (sample values: 'Germany', 'France', ...)`).
   - This ensures business terms like *"discontinued items"* or *"German customers"* resolve to exact database column values without guessing.
3. **Golden Queries (Few-Shot Retrieval)**:
   - Hand-curated SQL queries representing complex multi-join patterns, aggregations, and edge cases are indexed in `data/golden_queries.json`.
   - `retrieve_golden_queries(question, top_k=2)` finds the most structurally similar examples and injects them as few-shot guides in `plan()`.

### 🔄 Rebuilding Grounding Indexes
You can rebuild all three layers (column value profiles, schema embeddings, and golden queries) in a single pass with the CLI tool:

```bash
python -m app.index_schema
```

---

## 🔁 4-Step Agentic Pipeline

1. **`plan(question, schema, few_shot_examples, error_context)`**:
   - Calls the LLM with grounded schema context and few-shot golden examples. Generates a structured JSON plan with step-by-step reasoning (`reasoning_plan`), dialect tag (`sql_dialect`), and SQL query (`sql_query`).
2. **`execute_sql(query, max_rows=500, timeout_seconds=5)`**:
   - Executes against the read-only PostgreSQL connection.
   - **Guardrails**: Enforces strictly `SELECT` statements (blocks destructive DDL/DML), 500-row limit, and 5-second statement timeout.
3. **`answer_question(question, max_retries=3)` (Orchestrator Loop)**:
   - Coordinates grounded planning and execution. If execution fails, feeds the exact error back into `plan()` for self-correction up to `max_retries`.
4. **`synthesize_answer(question, columns, rows)`**:
   - Separate, lightweight LLM call that translates query results into a concise, human-friendly natural language answer.

---

## 🛡️ Safety, Correctness & Guardrails

The system enforces multi-layered defense-in-depth across database access, query parsing, dialect compliance, and rate limiting:

1. **Database-Level Read-Only Role**:
   - `app/db.py` connects to PostgreSQL via a dedicated `sql_readonly` role (`READONLY_DATABASE_URL`).
   - The user has only `SELECT` privileges on tables and sequences in `schema public`. Write, truncate, drop, and alter commands are revoked at the PostgreSQL engine level.

2. **AST Parsing & Prohibited Statement Blocking (`sqlglot`)**:
   - `app/validator.py` inspects the Abstract Syntax Tree (AST) using `sqlglot`.
   - Rejects any query containing `Insert`, `Update`, `Delete`, `Drop`, `Alter`, `Create`, `Truncate`, `Grant`, `Revoke`, `Pragma`, or administrative commands.
   - Blocks multiple statements in a single execution (`SELECT 1; DROP TABLE ...`).
   - Blocks dangerous server-side functions (e.g., `pg_sleep`, `pg_terminate_backend`, `dblink`, `xp_cmdshell`).

3. **Strict Dialect Enforcement**:
   - The target dialect (`config.SQL_DIALECT`, default: `postgres`) is enforced in the prompt and verified via AST transpilation before execution.
   - Non-compliant constructs (e.g. SQL Server `SELECT TOP 10` or `DATEDIFF` when targeting Postgres) are immediately caught and fed back into the self-correction retry loop.

4. **Resource Bounds & Timeouts**:
   - Hard row limit of 500 rows per query execution.
   - 5-second database statement timeout (`SET LOCAL statement_timeout = 5000`).

5. **Rate Limiting**:
   - Sliding-window rate limiter per client IP (`RATE_LIMIT_PER_MINUTE`, default: 60 queries/min) returning `HTTP 429 Too Many Requests` when exceeded.

6. **Structured Audit Logging & Cost Tracking**:
   - Every question, plan, generated SQL, AST validation status, dialect check result, execution status, latency, token count, and cost estimate is logged into an append-only SQLite audit log (`vector_cache/audit.db`).
   - Inspectable via `GET /audit?limit=50`.

---

## 📡 API Endpoints

### `POST /ask`
Submit a natural language question to the database.

**Request:**
```json
{
  "question": "Which customers are located in Germany?",
  "max_retries": 3
}
```

**Response:**
```json
{
  "answer": "The customers located in Germany are Alfreds Futterkiste and Blauer See Delikatessen.",
  "sql_attempts": [
    {
      "attempt": 1,
      "reasoning_plan": "Filter customers table where country is Germany and select company_name.",
      "sql_query": "SELECT company_name FROM customers WHERE country = 'Germany';",
      "success": true,
      "error": null,
      "row_count": 2,
      "ast_valid": true,
      "dialect_valid": true,
      "latency_ms": 32.5
    }
  ],
  "final_sql": "SELECT company_name FROM customers WHERE country = 'Germany';",
  "rows": [
    {"company_name": "Alfreds Futterkiste"},
    {"company_name": "Blauer See Delikatessen"}
  ],
  "columns": ["company_name"],
  "total_tokens_used": 185,
  "estimated_cost_usd": 0.00072,
  "total_latency_ms": 412.0
}
```

### `GET /audit`
Fetch recent audit log records for monitoring and compliance.

### `GET /health`
Returns connection status to the database, target dialect, rate limits, and LLM configuration.

---

## 🧪 Running Tests

Run the complete test suite with pytest:
```bash
pytest -v -o asyncio_mode=auto
```

---

## 🗺️ Roadmap
- [x] **Phase 0**: Project skeleton, configuration, database pool & read-only setup
- [x] **Phase 1**: Core 4-step plan-generate-execute-retry-synthesize loop & `/ask` endpoint
- [x] **Phase 2**: Three layers of anti-hallucination grounding (Schema-linking + Value hinting + Golden queries)
- [x] **Phase 3**: Safety guardrails & dialect enforcement (read-only role, AST parser, rate limiter, audit log)
- [ ] **Phase 4**: Evaluation harness & benchmarking

