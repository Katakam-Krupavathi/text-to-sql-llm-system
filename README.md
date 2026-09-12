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

## 🧪 Running Tests

Run the test suite with pytest:
```bash
pytest
```

---

## 🗺️ Roadmap
- [x] **Phase 0**: Project skeleton, configuration, database pool & read-only setup
- [ ] **Phase 1**: Dynamic schema extraction & indexing
- [ ] **Phase 2**: Schema-linking & few-shot context retrieval
- [ ] **Phase 3**: LLM generation with SQL dialect enforcement & AST parsing
- [ ] **Phase 4**: Read-only query execution & self-correction error loop
- [ ] **Phase 5**: Evaluation harness & benchmarking
