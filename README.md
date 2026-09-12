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

## 🧠 Core 4-Step Agentic Pipeline

The system processes questions through a robust 4-step agent pipeline:

1. **`plan(question, schema, error_context)`**:
   - Calls the LLM to generate a structured JSON plan with step-by-step reasoning (`reasoning_plan`), dialect tag (`sql_dialect`), and SQL query (`sql_query`).
   - Forces chain-of-thought reasoning over tables, joins, filters, and aggregations before generating SQL.
2. **`execute_sql(query, max_rows=500, timeout_seconds=5)`**:
   - Executes the query against the read-only database connection.
   - Guardrail checks: Enforces strictly `SELECT` statements (disallowing destructive DDL/DML), 500-row limit, and 5-second statement timeout.
3. **`answer_question(question, max_retries=3)` (Orchestrator Loop)**:
   - Orchestrates planning and execution.
   - If execution fails (syntax errors, non-existent columns/tables), it captures the exact database error, feeds it back into `plan()` as context, and self-corrects up to `max_retries`.
   - Returns a complete execution trace across all attempts.
4. **`synthesize_answer(question, columns, rows)`**:
   - Separate, lightweight LLM call that receives query results and translates them into a coherent, user-friendly natural language response.

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
      "row_count": 2
    }
  ],
  "final_sql": "SELECT company_name FROM customers WHERE country = 'Germany';",
  "rows": [
    {"company_name": "Alfreds Futterkiste"},
    {"company_name": "Blauer See Delikatessen"}
  ],
  "columns": ["company_name"]
}
```

### `GET /health`
Returns connection status to the database, target dialect, and LLM configuration.

---

## 🧪 Running Tests

Run the test suite with pytest:
```bash
pytest -v -o asyncio_mode=auto
```

---

## 🗺️ Roadmap
- [x] **Phase 0**: Project skeleton, configuration, database pool & read-only setup
- [x] **Phase 1**: Core 4-step plan-generate-execute-retry-synthesize loop & `/ask` endpoint
- [ ] **Phase 2**: Schema-linking & few-shot context retrieval
- [ ] **Phase 3**: Advanced SQL dialect enforcement & AST parsing
- [ ] **Phase 4**: Evaluation harness & benchmarking

