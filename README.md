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
                                  +---------------------------------------+
                                  |     Client (REST API / Streamlit)     |
                                  +-------------------+-------------------+
                                                      |
                                                      v
                                  +---------------------------------------+
                                  |     🔐 Auth & JWT Gating Layer        |
                                  |  (POST /auth/register, /auth/login)   |
                                  +-------------------+-------------------+
                                                      |
                                                      v
                                  +---------------------------------------+
                                  |   📂 Per-User DatabaseConnection      |
                                  |  - AES-256 Symmetric Decryption       |
                                  |  - Dialect Resolution (PG/SQLite/etc) |
                                  +-------------------+-------------------+
                                                      |
                       +------------------------------+------------------------------+
                       |                                                             |
                       v                                                             v
       [📖 READ PATH: POST /ask]                                     [✍️ WRITE PATH: POST /ask/write]
+---------------------------------------------+               +---------------------------------------------+
| 🛡️ Isolated Grounding (Per-Connection)      |               | 1. Plan Write Query                         |
| • Schema-Linking (Table/Column vectors)     |               |    • Strictly INSERT / UPDATE / DELETE      |
| • Distinct Low-Cardinality Value Hints      |               |    • WHERE clause strictly required         |
| • Few-Shot Golden Query Retrieval           |               |                                             |
|                                             |               | 2. Write AST Validation (sqlglot)           |
| 🔁 Agentic Self-Correction Loop             |               |    • Blocks DROP/ALTER/TRUNCATE/multi-stmt  |
| 1. Plan SQL via Multi-LLM Router            |               |                                             |
|    (Anthropic -> OpenAI -> Gemini -> Groq)  |               | 3. Dry-Run Preview (Read-Only Connection)   |
| 2. Read-Only AST Guardrail (sqlglot)        |               |    • UPDATE/DELETE -> Runs SELECT preview   |
| 3. Safe DB Execution (Read-Only Role)       |               |    • INSERT -> Formats prospective rows     |
| 4. Self-Correction on Dialect/DB Error      |               |                                             |
| 5. Synthesize Natural-Language Answer       |               | 4. Mint 5-Minute Signed Preview Token       |
+----------------------+----------------------+               +----------------------+----------------------+
                       |                                                             |
                       |                                                             v (User reviews preview)
                       |                                              +---------------------------------------------+
                       |                                              | 🚀 POST /ask/write/confirm {preview_token}  |
                       |                                              | • Cryptographically verifies token & expiry |
                       |                                              | • Re-validates Write AST                    |
                       |                                              | • Dedicated Write-Capable Role              |
                       |                                              | • Explicit ACID Transaction (BEGIN/COMMIT)  |
                       |                                              | • Automatic Rollback on Any Failure         |
                       +------------------------------+---------------+---------------------------------------------+
                                                      |
                                                      v
                                  +---------------------------------------+
                                  |   📝 Append-Only Audit Trail (SQLite) |
                                  |  • Reads: Plans, Latency, Token Cost  |
                                  |  • Writes: User, Mutating SQL, Rows   |
                                  +---------------------------------------+
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
- **Cohesive Slate/Dark Theme**: Styled developer visual identity configured via `.streamlit/config.toml`.
- **Custom Chat Avatars & Subtitles**: Distinct user (`🧑‍💻`) and assistant (`🤖`) chat bubbles and architecture headers.
- **Interactive Multi-Tenant Auth**: Seamless sidebar Login & Registration tabs with JWT session management and token swap expanders.
- **Structured Sidebar Containers**: Organized `st.container(border=True)` cards for system health, user auth, connection switcher, session memory reset, and collapsed sample questions.
- **✍️ Data Modification Mode (Opt-In Safe Writes)**: Toggle between read-only questions and mutating queries with chain-of-thought plans, AST-validated SQL, tabular previews ("nothing changed yet"), estimated affected rows, and one-click **"Confirm & Execute Write"** (with audit logging) / **"Discard"** action buttons.
- **Visible Reasoning Trace**: Expandable dropdown showing chain-of-thought plans, per-attempt self-corrections, and verified SQL.
- **Interactive Dataframes & Auto-Charting**: Tabular result viewer with automatic bar and trend line charts for numeric query outputs.
- **Performance Badges**: Attempt counts, self-correction indicator, latency in ms, and token cost breakdown.

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

## 🔐 Multi-Tenant Auth & Bring-Your-Own-Database (BYODB)

The system supports multi-tenant isolation, user authentication, and secure dynamic database connections:

1. **User Accounts & Authentication**:
   - Register via `POST /auth/register` and login via `POST /auth/login` to obtain JWT bearer access tokens.
   - Secure password hashing with `passlib` (bcrypt).
2. **Symmetric Encryption at Rest**:
   - Connection strings containing database passwords are encrypted at rest using AES/Fernet encryption (`ENCRYPTION_KEY`). Decrypted credentials are never logged or exposed.
3. **Bring-Your-Own-Database (`POST /connections`)**:
   - Users can connect any SQL database (PostgreSQL, SQLite, MySQL, Snowflake).
   - **Connection-Time Validation**: The system validates connectivity and executes a lightweight test query (`SELECT 1`) upon creation. Unreachable or invalid databases are rejected with clear error messages.
   - **Write-Probe Safety Warning**: Probes database permissions on registration. If write privileges are detected, the system issues a plain safety recommendation advising the use of a read-only user without blocking execution.
4. **Per-Connection Isolated Grounding**:
   - Schema indexes, profiled low-cardinality value hints, and few-shot golden queries are strictly namespaced by `connection_id` in `vector_cache/connections/{connection_id}/`.
   - Complete tenant isolation: User A's queries and schema data are never leaked or accessible to User B.

---

## 🔌 Multi-LLM Router & Fallback Chain

The system includes a resilient multi-provider LLM router supporting priority ordering and automatic failover across major models:

- **Supported Providers**:
  - **Anthropic**: Claude 3.5 Sonnet (`claude-3-5-sonnet-latest`)
  - **OpenAI**: GPT-4o / GPT-4o Mini (`gpt-4o`, `gpt-4o-mini`)
  - **Google Gemini**: Gemini 1.5 Pro / Flash (`gemini-1.5-pro`, `gemini-1.5-flash`)
  - **Groq**: Fast open-weights inference (`llama-3.3-70b-versatile`)
- **Priority-Ordered Fallback**: Configured via `LLM_PROVIDER_ORDER=anthropic,openai,gemini,groq`. If the primary provider encounters rate limits (`HTTP 429`), timeouts, quota limits, or 5xx server errors, the router logs the incident and transparently falls back to the next provider in the chain.
---

## ✍️ Safe Write Operations Flow (Opt-In Data Modification)

The system provides a distinct, opt-in path for data modification statements (`INSERT`, `UPDATE`, `DELETE`) with strict guardrails, isolated completely from the default read-only execution path:

```
Step 1: User Request -> POST /ask/write
                        ├── Checks connection allow_writes == true
                        ├── AST Validation (Only INSERT/UPDATE/DELETE; WHERE strictly required)
                        ├── Dry-Run Preview (Runs equivalent SELECT without mutating data)
                        └── Returns Preview Rows + 5-Minute Signed Preview Token

Step 2: Review & Approval -> POST /ask/write/confirm {preview_token}
                             ├── Cryptographically validates token & expiry
                             ├── Re-validates Write AST before execution
                             ├── Executes inside an explicit Database Transaction (ACID)
                             ├── Commits on success; Rolls back on any failure
                             └── Audits execution with write flag & affected row count
```

### Key Safety Guarantees & Constraints:
1. **Opt-In Per Connection (`allow_writes: bool`)**:
   - Write operations are disabled by default (`allow_writes=False`).
   - `POST /ask/write` strictly rejects mutating requests against connections where `allow_writes` is `false`.
2. **Dedicated Write-Capable Role Requirement**:
   - Write confirmation connects using an explicitly-provisioned write-capable database user/role (configured in the connection string or via `DATABASE_URL`), strictly separated from the read-only database role used by `POST /ask` and preview steps.
3. **Mandatory `WHERE` Clauses**:
   - `UPDATE` and `DELETE` queries **strictly require a `WHERE` clause** at the AST validator level. Unconditional mass updates or mass deletes are rejected outright before ever reaching execution.
4. **Zero Accidental Mutations (Dry-Run Preview)**:
   - `POST /ask/write` never executes mutating SQL directly. For `UPDATE` and `DELETE`, it executes an equivalent read-only `SELECT * FROM <table> WHERE <conditions> LIMIT 100` against the read-only connection to return a preview of affected rows. For `INSERT`, it formats and returns the exact records to be inserted.
5. **Token-Bound Confirmation & Re-Validation**:
   - `POST /ask/write/confirm` requires a short-lived (5-minute) preview token bound to the exact SQL string. The SQL is re-validated through the write AST guardrail before transaction execution.
6. **Transaction Isolation & Rollback**:
   - Writes execute inside an explicit database transaction block (`BEGIN ... COMMIT`). If an error occurs, the transaction is immediately rolled back and the database state remains untouched.
7. **Strict Write Audit Trail**:
   - Every confirmed write is logged to the audit log (`app/audit.py`) with `is_write=1`, user ID, exact SQL statement, execution latency, and affected row count.

---

## 🛡️ Multi-Tenant & Write Support Safety Model

The system is built on a strict defense-in-depth model that guarantees tenant isolation and execution safety across read and write operations:

### 1. Multi-Tenant Isolation & Storage Security
- **Authentication**: All custom connection management and write endpoints require valid JWT authentication (`Authorization: Bearer <token>`).
- **Symmetric Encryption at Rest**: All database connection strings are encrypted at rest with AES-256 (`cryptography.fernet`) using `ENCRYPTION_KEY`. Plaintext credentials and passwords are never logged, persisted in plaintext, or returned in API responses.
- **Namespaced Grounding Vector Caches**: Schema embeddings, low-cardinality value profiles, and golden queries are partitioned in isolated directories (`vector_cache/connections/{connection_id}/`). Query contexts and schemas from User A cannot be retrieved by User B.

### 2. BYODB Read-Only Guarantees & Credentials Caveat
> [!IMPORTANT]
> **Safety Caveat on User-Supplied Database Credentials:**
> Once Bring-Your-Own-Database (BYODB) is enabled, read-only guarantees depend in part on the permissions granted to the database user in the supplied connection string:
> - **AST-Level Guardrail**: The system parses every SQL AST with `sqlglot` before execution, strictly blocking DDL, DML, and dangerous system functions (`DROP`, `ALTER`, `TRUNCATE`, `pg_sleep`, `dblink`, etc.).
> - **Write-Probe Advisory**: When a connection is registered, the system performs an active write probe (`CREATE/DROP TEMPORARY TABLE`). If write privileges are detected, the system records `is_read_only = False` and issues a safety recommendation advising the user to provision a dedicated read-only database role.
> - **Best Practice Recommendation**: For production deployments, users should always configure custom database credentials using a dedicated read-only role (`GRANT SELECT ON ALL TABLES ...`) to ensure hardware-level isolation even in the event of unexpected parser edge cases.

### 3. Opt-In Two-Phase Write Safety Model
- **Opt-In Flag (`allow_writes: bool`)**: Connections default to `allow_writes = False`. Mutating requests against connections without this flag are rejected with `HTTP 403 Forbidden`.
- **Mandatory `WHERE` Clause**: Mass updates and mass deletes without a `WHERE` clause are rejected at the AST level with zero overrides.
- **Dry-Run Preview**: `POST /ask/write` executes an equivalent `SELECT ... WHERE <conditions> LIMIT 100` against the read-only connection, returning preview rows without altering any database records.
- **Cryptographic Token Binding**: Previews generate a 5-minute signed JWT token bound to the exact SQL statement.
- **ACID Transaction Commit & Rollback**: `POST /ask/write/confirm` re-validates the AST and executes the write inside a transactional block (`BEGIN ... COMMIT`), automatically issuing a `ROLLBACK` on any database error.
- **Comprehensive Audit Trail**: Writes are recorded in `audit_logs` with `is_write = 1`, user ID, exact SQL, affected rows, and execution latency.

---

## 🗺️ Roadmap

- [x] **Core Text-to-SQL Agent**: Self-correction loop with sqlglot AST verification and natural language synthesis.
- [x] **3-Layer Anti-Hallucination Grounding**: Dynamic schema-linking, column value hinting, and few-shot golden queries.
- [x] **Dialect Enforcement**: Syntax and function validation across PostgreSQL, SQLite, MySQL, and Snowflake.
- [x] **Conversational Memory**: Multi-turn context tracking and pronoun/reference resolution.
- [x] **Interactive Streamlit Web UI**: Visible reasoning trace expander, interactive dataframes, and auto-charting.
- [x] **Automated Benchmark Harness**: Execution Accuracy (EX) evaluation suite and CI gate threshold enforcement.
- [x] **Multi-LLM Fallback Router**: Priority-ordered failover across Anthropic, OpenAI, Gemini, and Groq.
- [x] **Multi-Tenant Auth & BYODB**: JWT authentication, AES-256 credential encryption, connection testing, and isolated schema vector indexes.
- [x] **Safe Opt-In Write Operations**: Two-phase preview and transaction confirmation flow for INSERT/UPDATE/DELETE with mandatory WHERE clauses.

---

## 🧪 Testing

Run the full test suite with pytest (51 unit & integration tests):
```bash
pytest -v -o asyncio_mode=auto
```


