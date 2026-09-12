# 🤖 Enterprise Text-to-SQL Agent

[![CI & Execution Accuracy Benchmark](https://github.com/Katakam-Krupavathi/text-to-sql-llm-system/actions/workflows/eval.yml/badge.svg)](https://github.com/Katakam-Krupavathi/text-to-sql-llm-system/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.32%2B-FF4B4B.svg)](https://streamlit.io/)

A production-grade, self-correcting **Text-to-SQL system** engineered with multi-layered grounding, strict AST-level safety guardrails, dialect enforcement, multi-turn conversational memory, and an interactive Streamlit UI.

---

## 🏛️ System Architecture

```text
                                  +-----------------------+
                                  |     User Question     |
                                  +-----------+-----------+
                                              |
                                              v
+------------------------------------------------------------------------------------------+
|  🛡️ 3-Layer Anti-Hallucination Grounding Retrieval                                       |
|  1. Schema-Linking: Vector similarity extracts exact target tables/columns               |
|  2. Value Hinting: Distinct low-cardinality values profiled at index time                |
|  3. Golden Queries: Top-k structurally similar SQL few-shot examples                     |
+---------------------------------------------+--------------------------------------------+
                                              |
                                              v
+------------------------------------------------------------------------------------------+
|  🔁 4-Step Agentic Self-Correction Loop                                                 |
|                                                                                          |
|    [Step 1: Plan] --------> [Step 2: AST Guardrail] ----> [Step 3: Safe Execution]      |
|    • Chain-of-Thought       • sqlglot AST verification     • PostgreSQL read-only role   |
|    • Dialect target         • Prohibits DDL/DML            • Max 500 rows                |
|                             • Dialect syntax check         • 5-second statement timeout  |
|                                     |                                   |                |
|                                     +---- (On Syntax/DB Error) ---------+                |
|                                     |     Captures error trace & re-enters               |
|                                     v     plan() for self-correction                     |
|                                                                                          |
|    [Step 4: Synthesize Answer] <---------------------------------------+                 |
|    • Translates raw SQL result rows into clear natural language sentence                 |
+---------------------------------------------+--------------------------------------------+
                                              |
                                              v
+------------------------------------------------------------------------------------------+
|  📡 Output & Interfaces                                                                  |
|  • Streamlit Web UI: Visible reasoning trace, dataframes & auto-charting                 |
|  • FastAPI REST API: POST /ask, GET /audit, GET /health, DELETE /sessions                |
|  • Append-Only SQLite Audit Logger: Query traces, token counts, cost & latency           |
+------------------------------------------------------------------------------------------+
```

---

## 🚀 Quickstart

### 🐳 Option A: One-Command Docker Demo (Recommended)

Run the full stack (PostgreSQL, database schema seeder, read-only role setup, grounding index builder, FastAPI backend, and Streamlit UI) in a single command:

```bash
# 1. Clone the repository and configure environment keys
git clone https://github.com/Katakam-Krupavathi/text-to-sql-llm-system.git
cd text-to-sql-llm-system
cp .env.example .env

# 2. Launch containerized stack
make demo
# Or: docker compose up --build
```

**Service URLs:**
- **Streamlit Web UI**: [http://localhost:8501](http://localhost:8501)
- **FastAPI Interactive Docs (Swagger)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health & Dialect Status**: [http://localhost:8000/health](http://localhost:8000/health)

To stop the stack:
```bash
make down
```

---

### 💻 Option B: Local Development (Without Docker)

#### 1. Prerequisites
- Python 3.10+
- PostgreSQL database running locally or via Docker

#### 2. Setup Virtual Environment
```bash
python -m venv env
source env/bin/activate  # On Windows: env\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

#### 3. Database Initialization & Grounding Setup
```bash
# 1. Seed sample database (E-commerce / Northwind schema)
python scripts/seed_sample_db.py --dbname text_to_sql_db --user postgres --password postgres

# 2. Provision dedicated read-only role with SELECT-only grants
python scripts/setup_readonly_role.py --dbname text_to_sql_db --admin-user postgres --admin-password postgres

# 3. Profile low-cardinality values & build grounding vector indexes
python -m app.index_schema
```

#### 4. Run Backend & Frontend
```bash
# Terminal 1: FastAPI Server
uvicorn app.main:app --reload --port 8000

# Terminal 2: Streamlit UI
streamlit run app/ui.py
```

---

## 🛡️ Safety, Guardrails & Correctness

The system implements defense-in-depth across database access, query parsing, dialect compliance, and rate limiting:

1. **Database-Level Read-Only Role (`sql_readonly`)**:
   - Agent connections use a dedicated PostgreSQL user with only `SELECT` grants on public tables/sequences. Write, update, delete, drop, and alter permissions are revoked at the PostgreSQL engine level.
2. **AST Parsing & Security Filtering (`sqlglot`)**:
   - Parses the Abstract Syntax Tree (AST) before sending SQL to the engine.
   - Rejects any query containing `Insert`, `Update`, `Delete`, `Drop`, `Alter`, `Create`, `Truncate`, `Grant`, `Revoke`, `Pragma`, or administrative commands.
   - Disallows multiple statements (`SELECT 1; DROP TABLE ...`).
   - Blocks dangerous functions (`pg_sleep`, `pg_terminate_backend`, `dblink`, `xp_cmdshell`).
3. **Dialect Enforcement & Transpilation**:
   - Enforces target dialect (`config.SQL_DIALECT`, default: `postgres`) in the planning prompt and verifies AST syntax. Non-compliant keywords (e.g. SQL Server `TOP n` or `DATEDIFF`) trigger automatic self-correction.
4. **Rate Limiting & Resource Constraints**:
   - Sliding-window rate limiter (`RATE_LIMIT_PER_MINUTE`, default: 60 queries/min).
   - Hard row limit (500 rows) and statement timeout (5 seconds).
5. **Structured Audit Logging**:
   - SQLite append-only audit logger (`vector_cache/audit.db`) recording query plans, SQL attempts, validation outcomes, execution latency, token counts, and cost estimates.

---

## 🧠 Grounding & Anti-Hallucination

1. **Schema-Linking (Structural Retrieval)**: Vector index over tables and columns retrieves only the necessary tables for a given question (`relevant_schema(question)`).
2. **Value Hinting (Column Data Profiling)**: Distinct values for low-cardinality columns (e.g. status, countries, categories) are extracted at index time and injected directly into schema prompts (`country VARCHAR (sample values: 'Germany', 'France', ...)`).
3. **Golden Queries (Few-Shot Retrieval)**: Curated SQL patterns from `data/golden_queries.json` are retrieved based on query similarity and injected as few-shot guides.
4. **Dual Embedding Backend Support**:
   - **`keyword`** (Default): Normalized sparse TF-IDF token frequency vectors. Fast, deterministic, and works completely offline without API dependencies.
   - **`openai`**: Dense vectors via OpenAI `text-embedding-3-small` for semantic similarity over paraphrases. Configured via `EMBEDDING_BACKEND=openai` in `.env`.

---

## 💬 Multi-Turn Conversation Memory

The agent supports conversational follow-up questions (e.g., resolving pronouns like *"that"* or *"those customers"*):

**Turn 1:**
```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the total freight shipping cost by country?", "session_id": "sess-1"}'
```
*Generated SQL:* `SELECT ship_country, SUM(freight) FROM orders GROUP BY ship_country;`

**Turn 2 (Follow-up):**
```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Now just show me that for Germany", "session_id": "sess-1"}'
```
*Generated SQL:* `SELECT ship_country, SUM(freight) FROM orders WHERE ship_country = 'Germany' GROUP BY ship_country;`

---

## 🖥️ Streamlit Web Interface

Launch with `streamlit run app/ui.py`:
- **Visible Reasoning Trace**: Expandable dropdown showing the retrieved schema, chain-of-thought plan, SQL attempts, and final verified SQL.
- **Interactive Dataframes**: Full tabular result viewer.
- **Auto-Charting**: Automatic bar charts and line charts for numeric query outputs.
- **Performance Badges**: Attempt counts, self-correction indicator, latency, and token cost.

---

## 📊 Evaluation & Benchmark Harness

The repository includes an automated evaluation harness in [`scripts/run_eval.py`](file:///c:/Users/krupa/OneDrive/Desktop/projects/text-to-sql-llm-system/scripts/run_eval.py) implementing **Execution Accuracy (EX)** across 22 benchmark queries ([`tests/eval/eval_set.json`](file:///c:/Users/krupa/OneDrive/Desktop/projects/text-to-sql-llm-system/tests/eval/eval_set.json)).

### Benchmark Results:
- **Benchmark Suite**: 22 diverse queries (aggregations, multi-table joins, date ranges, value-hinted filters, ranking).
- **Execution Accuracy (EX)**: **100% (22/22 Passing)** against sample Northwind schema.
- **CI Gate Threshold**: Enforced at **>= 70%** in GitHub Actions workflow ([`.github/workflows/eval.yml`](file:///c:/Users/krupa/OneDrive/Desktop/projects/text-to-sql-llm-system/.github/workflows/eval.yml)).

Run locally:
```bash
python scripts/run_eval.py --eval-set tests/eval/eval_set.json --threshold 0.70
```

---

## 🧪 Testing

Run the test suite with pytest (38 unit & integration tests):
```bash
pytest -v -o asyncio_mode=auto
```

---

## 🗺️ Roadmap & Implementation Status

- [x] **Phase 0**: Project skeleton, configuration, database connection pool & read-only setup
- [x] **Phase 1**: Core 4-step plan-generate-execute-retry-synthesize loop & `POST /ask` endpoint
- [x] **Phase 2**: Three layers of anti-hallucination grounding (Schema-linking + Value hinting + Golden queries)
- [x] **Phase 3**: Safety guardrails & dialect enforcement (read-only role, AST parser, rate limiter, audit log)
- [x] **Phase 4**: Multi-turn conversation memory & follow-up reference resolution
- [x] **Phase 5**: Interactive Streamlit UI with visible reasoning trace, auto-charting, and dataframes
- [x] **Phase 6**: Evaluation harness (Execution Accuracy EX) & GitHub Actions CI gating
- [x] **Phase 7**: Docker containerization & one-command demo stack (`make demo`)
