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

## 🚀 Quickstart

### 🐳 Option A: One-Command Docker Demo (Recommended)

Start the entire stack (PostgreSQL, automatic schema seeding, read-only role provisioning, grounding index builder, FastAPI backend, and Streamlit UI) in one command:

```bash
# 1. Configure environment keys
cp .env.example .env

# 2. Launch the containerized stack
make demo
# Or: docker compose up --build
```

Access the applications at:
- **Streamlit Web UI**: [http://localhost:8501](http://localhost:8501)
- **FastAPI Documentation & Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

To stop the stack:
```bash
make down
```

---

### 💻 Option B: Local Development (Without Docker)

#### 1. Prerequisites
- Python 3.10+
- PostgreSQL running locally or in a container

#### 2. Environment Setup
```bash
python -m venv env
source env/bin/activate  # On Windows: env\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

#### 3. Database Initialization & Read-Only Role
```bash
# Seed sample relational schema and data (Northwind/E-commerce)
python scripts/seed_sample_db.py --dbname text_to_sql_db --user postgres --password postgres

# Provision dedicated read-only role for safe query execution
python scripts/setup_readonly_role.py --dbname text_to_sql_db --admin-user postgres --admin-password postgres

# Profile data, generate value hints, and build grounding vector indexes
python -m app.index_schema
```

#### 4. Running the Backend and UI
```bash
# Terminal 1: FastAPI Backend
uvicorn app.main:app --reload --port 8000

# Terminal 2: Streamlit UI
streamlit run app/ui.py
```

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
Submit a natural language question to the database. Supports multi-turn follow-up conversations using `session_id`.

**Turn 1: Initial Question**
```json
{
  "question": "What is the total freight shipping cost by country?",
  "session_id": "user-session-123"
}
```

**Response 1:**
```json
{
  "session_id": "user-session-123",
  "answer": "Total freight by country: Germany is $84.55, Mexico is $77.44, Spain is $100.19, etc.",
  "final_sql": "SELECT ship_country, SUM(freight) AS total_freight FROM orders GROUP BY ship_country;",
  "rows": [
    {"ship_country": "Germany", "total_freight": 84.55},
    {"ship_country": "Mexico", "total_freight": 77.44}
  ],
  "columns": ["ship_country", "total_freight"]
}
```

**Turn 2: Follow-up Question (Using Memory)**
```json
{
  "question": "Now just show me that for Germany",
  "session_id": "user-session-123"
}
```

**Response 2:**
```json
{
  "session_id": "user-session-123",
  "answer": "The total freight shipping cost for Germany is $84.55.",
  "final_sql": "SELECT ship_country, SUM(freight) AS total_freight FROM orders WHERE ship_country = 'Germany' GROUP BY ship_country;",
  "rows": [
    {"ship_country": "Germany", "total_freight": 84.55}
  ],
  "columns": ["ship_country", "total_freight"]
}
```

### `DELETE /sessions/{session_id}`
Clears conversation history for a specific session.

### `GET /audit`
Fetch recent audit log records for monitoring and compliance.

## 🖥️ Interactive Web UI (Streamlit)

The system includes a chat interface built with Streamlit in [`app/ui.py`](file:///c:/Users/krupa/OneDrive/Desktop/projects/text-to-sql-llm-system/app/ui.py):

- **Natural Language Chat**: Clean message thread supporting follow-up questions with conversational memory.
- **Visible Reasoning Trace ("Thought Process")**: Expandable dropdown revealing:
  - Retrieved schema definitions and sample value hints
  - Model reasoning plan (chain-of-thought)
  - Full history of SQL generation attempts, errors, and self-corrections
  - Verified final executable SQL with syntax highlighting
- **Interactive Dataframes**: Renders full query result sets in tabular format.
- **Automatic Visualizations**: Auto-detects categorical + numeric dimensions and renders bar/line charts.
- **Performance Badges**: Displays execution attempt count, latency, tokens used, and estimated cost.

```text
+-----------------------------------------------------------------------+
|  🤖 Enterprise Text-to-SQL Agent                                      |
|                                                                       |
|  User: What is the total revenue by product category?                 |
|                                                                       |
|  Assistant: Total revenue by category is led by Dairy Products        |
|             ($54,200) followed by Beverages ($48,150)...              |
|                                                                       |
|  🎯 Resolved on 1st attempt | ⏱️ 420 ms | 🪙 230 tokens ($0.00085)     |
|                                                                       |
|  ▼ 🔍 Thought Process & Agent Reasoning Trace                         |
|     • Reasoning: Join categories, products, order_items...            |
|     • SQL: SELECT c.category_name, SUM(...) FROM categories c...      |
|     • Status: ✓ Validated & Executed (8 rows returned)                |
|                                                                       |
|  [ Dataframe: category_name | category_revenue ]                      |
|  [ Bar Chart: category_revenue by category_name ]                     |
+-----------------------------------------------------------------------+
```

### Launching the Web UI

1. Make sure the FastAPI backend is running:
```bash
uvicorn app.main:app --reload --port 8000
```

2. In a separate terminal, launch the Streamlit frontend:
```bash
streamlit run app/ui.py
```
Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## 📊 Evaluation & Benchmark Harness

The repository includes an automated evaluation benchmark in [`scripts/run_eval.py`](file:///c:/Users/krupa/OneDrive/Desktop/projects/text-to-sql-llm-system/scripts/run_eval.py) implementing **Execution Accuracy (EX)** across a curated set of 22 test queries ([`tests/eval/eval_set.json`](file:///c:/Users/krupa/OneDrive/Desktop/projects/text-to-sql-llm-system/tests/eval/eval_set.json)).

### How Execution Accuracy (EX) Works:
1. The agent generates SQL for each test prompt via the 4-step grounded pipeline.
2. Both the agent's SQL and the ground-truth gold SQL are executed against the database.
3. Result sets are normalized and compared as order-independent multisets of tuples (verifying true semantic and data equivalence regardless of column ordering or alias differences).
4. CI pipeline (`.github/workflows/eval.yml`) enforces a minimum pass threshold (default: **70% EX**).

### Running Evaluation Benchmark Locally:
```bash
python scripts/run_eval.py --eval-set tests/eval/eval_set.json --threshold 0.70
```

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
- [x] **Phase 4**: Multi-turn conversation memory & follow-up reference resolution
- [x] **Phase 5**: Interactive Streamlit UI with visible reasoning trace, auto-charting, and dataframes
- [x] **Phase 6**: Evaluation harness (Execution Accuracy EX) & GitHub Actions CI gating

