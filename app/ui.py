import os
from typing import Any, Dict, List, Optional, Tuple
import uuid
import httpx
import pandas as pd
import streamlit as st

# Page configuration
st.set_page_config(
    page_title="Text-to-SQL Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Backend API configuration (supports Docker service discovery)
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")

# Initialize Session State
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state.messages = []

if "auth_token" not in st.session_state:
    st.session_state.auth_token = ""

if "user_email" not in st.session_state:
    st.session_state.user_email = ""

if "selected_connection_id" not in st.session_state:
    st.session_state.selected_connection_id = None


def check_backend_health():
    try:
        resp = httpx.get(f"{API_BASE_URL}/health", timeout=3.0)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def auth_login(email: str, password: str) -> Tuple[Optional[dict], Optional[str]]:
    try:
        resp = httpx.post(f"{API_BASE_URL}/auth/login", json={"email": email, "password": password}, timeout=10.0)
        if resp.status_code == 200:
            return resp.json(), None
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        return None, detail
    except httpx.ConnectError:
        return None, f"Could not connect to backend at {API_BASE_URL}."
    except Exception as e:
        return None, str(e)


def auth_register(email: str, password: str) -> Tuple[Optional[dict], Optional[str]]:
    try:
        resp = httpx.post(f"{API_BASE_URL}/auth/register", json={"email": email, "password": password}, timeout=10.0)
        if resp.status_code == 200:
            return resp.json(), None
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        return None, detail
    except httpx.ConnectError:
        return None, f"Could not connect to backend at {API_BASE_URL}."
    except Exception as e:
        return None, str(e)


def fetch_user_connections(auth_token: str):
    if not auth_token:
        return []
    try:
        headers = {"Authorization": f"Bearer {auth_token.strip()}"}
        resp = httpx.get(f"{API_BASE_URL}/connections", headers=headers, timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
        return []
    except Exception:
        return []


def send_query_to_backend(question: str, session_id: str, connection_id: Optional[str] = None, auth_token: Optional[str] = None):
    try:
        payload = {"question": question, "session_id": session_id}
        if connection_id:
            payload["connection_id"] = connection_id

        headers = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token.strip()}"

        resp = httpx.post(f"{API_BASE_URL}/ask", json=payload, headers=headers, timeout=60.0)
        if resp.status_code == 200:
            return resp.json(), None
        elif resp.status_code == 401:
            return None, "Authentication required (HTTP 401). Please enter a valid JWT token in the sidebar."
        elif resp.status_code == 403:
            return None, "Access denied (HTTP 403) for the specified database connection."
        elif resp.status_code == 429:
            return None, "Rate limit exceeded (HTTP 429). Please wait a moment before sending another query."
        else:
            return None, f"Backend Error ({resp.status_code}): {resp.text}"
    except httpx.ConnectError:
        return None, f"Could not connect to FastAPI backend at {API_BASE_URL}. Please make sure the server is running (`uvicorn app.main:app --port 8000`)."
    except Exception as e:
        return None, f"Unexpected error: {str(e)}"


def send_write_query_to_backend(question: str, session_id: str, connection_id: Optional[str] = None, auth_token: Optional[str] = None):
    try:
        payload = {"question": question, "session_id": session_id}
        if connection_id:
            payload["connection_id"] = connection_id

        headers = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token.strip()}"

        resp = httpx.post(f"{API_BASE_URL}/ask/write", json=payload, headers=headers, timeout=60.0)
        if resp.status_code == 200:
            return resp.json(), None
        elif resp.status_code == 401:
            return None, "Authentication required (HTTP 401). Please log in or provide a valid JWT token."
        elif resp.status_code == 403:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            if "allow_writes" in detail or "disabled" in detail:
                return None, "⚠️ Write operations are disabled for this database connection. Please edit your connection settings to enable writes (allow_writes=true), or switch to a write-enabled connection."
            return None, f"Access denied (HTTP 403): {detail}"
        elif resp.status_code == 429:
            return None, "Rate limit exceeded (HTTP 429). Please wait a moment before sending another query."
        else:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            return None, f"Write Planning Error ({resp.status_code}): {detail}"
    except httpx.ConnectError:
        return None, f"Could not connect to FastAPI backend at {API_BASE_URL}. Please make sure the server is running (`uvicorn app.main:app --port 8000`)."
    except Exception as e:
        return None, f"Unexpected error: {str(e)}"


def confirm_write_in_backend(preview_token: str, auth_token: Optional[str] = None):
    try:
        payload = {"preview_token": preview_token}
        headers = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token.strip()}"

        resp = httpx.post(f"{API_BASE_URL}/ask/write/confirm", json=payload, headers=headers, timeout=60.0)
        if resp.status_code == 200:
            return resp.json(), None
        elif resp.status_code == 409:
            return None, "⚠️ Replay Rejected: This write has already been executed or the preview token was already consumed."
        elif resp.status_code == 401:
            return None, "Authentication required (HTTP 401). Please log in."
        elif resp.status_code == 403:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            return None, f"Forbidden (HTTP 403): {detail}"
        else:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            return None, f"Write Execution Error ({resp.status_code}): {detail}"
    except httpx.ConnectError:
        return None, f"Could not connect to FastAPI backend at {API_BASE_URL}."
    except Exception as e:
        return None, f"Unexpected error: {str(e)}"


# --- Sidebar ---
with st.sidebar:
    st.title("⚙️ Agent Settings")
    
    # 1. Backend Health Status
    with st.container(border=True):
        st.subheader("🖥️ System Status")
        health = check_backend_health()
        if health:
            st.success(f"🟢 **Backend Online** ({health.get('dialect', 'postgres').upper()})")
            st.caption(f"Model: `{health.get('llm_model', 'gpt-4o')}` | Dialect: `{health.get('dialect')}`")
        else:
            st.error(f"🔴 **Backend Offline** ({API_BASE_URL})")
            st.caption(f"Start with: `uvicorn app.main:app --reload` (Target: `{API_BASE_URL}`)")

    # 2. Multi-Tenant Auth Section
    with st.container(border=True):
        st.subheader("🔐 User Authentication")
        if st.session_state.auth_token:
            user_display = st.session_state.user_email if st.session_state.user_email else "Authenticated User"
            st.success(f"👤 **{user_display}**")
            if st.button("🚪 Log Out", key="logout_btn", use_container_width=True):
                st.session_state.auth_token = ""
                st.session_state.user_email = ""
                st.session_state.selected_connection_id = None
                st.rerun()

            with st.expander("🔑 Advanced: Active Bearer Token", expanded=False):
                st.code(st.session_state.auth_token, language="text")
                new_jwt = st.text_input("Replace Token", value=st.session_state.auth_token, type="password", key="active_jwt_replace")
                if new_jwt != st.session_state.auth_token:
                    st.session_state.auth_token = new_jwt
                    st.rerun()
        else:
            tab_login, tab_register = st.tabs(["Log In", "Register"])
            with tab_login:
                with st.form("login_form"):
                    login_email = st.text_input("Email", placeholder="user@example.com", key="login_email")
                    login_pwd = st.text_input("Password", type="password", placeholder="••••••••", key="login_pwd")
                    login_btn = st.form_submit_button("Log In", type="primary", use_container_width=True)
                    if login_btn:
                        if not login_email or not login_pwd:
                            st.error("Please enter both email and password.")
                        else:
                            data, err = auth_login(login_email, login_pwd)
                            if err:
                                st.error(f"Login failed: {err}")
                            elif data:
                                st.session_state.auth_token = data.get("access_token", "")
                                st.session_state.user_email = data.get("email", login_email)
                                st.rerun()

            with tab_register:
                with st.form("register_form"):
                    reg_email = st.text_input("Email", placeholder="user@example.com", key="reg_email")
                    reg_pwd = st.text_input("Password (min 6 chars)", type="password", placeholder="••••••••", key="reg_pwd")
                    reg_btn = st.form_submit_button("Create Account", use_container_width=True)
                    if reg_btn:
                        if not reg_email or not reg_pwd:
                            st.error("Please enter email and password.")
                        elif len(reg_pwd) < 6:
                            st.error("Password must be at least 6 characters.")
                        else:
                            data, err = auth_register(reg_email, reg_pwd)
                            if err:
                                st.error(f"Registration failed: {err}")
                            elif data:
                                st.session_state.auth_token = data.get("access_token", "")
                                st.session_state.user_email = data.get("email", reg_email)
                                st.rerun()

            with st.expander("🔑 Advanced: Paste a token directly", expanded=False):
                raw_token = st.text_input(
                    "Paste JWT Bearer Token",
                    type="password",
                    help="Paste token from external auth",
                    key="raw_jwt_input",
                )
                if st.button("Apply Token", key="apply_raw_jwt_btn", use_container_width=True):
                    if raw_token.strip():
                        st.session_state.auth_token = raw_token.strip()
                        st.session_state.user_email = "Token User"
                        st.rerun()

    # 3. Connection Picker
    with st.container(border=True):
        st.subheader("🗄️ Database Connection")
        connections = fetch_user_connections(st.session_state.auth_token)
        if connections:
            conn_options = {f"{c['nickname']} ({c['dialect']})": c["id"] for c in connections}
            selected_label = st.selectbox("Select Active Connection", options=list(conn_options.keys()))
            st.session_state.selected_connection_id = conn_options.get(selected_label)
            selected_conn = next((c for c in connections if c["id"] == st.session_state.selected_connection_id), None)
            if selected_conn:
                if selected_conn.get("allow_writes", False):
                    st.caption("⚡ **Write-capable connection**")
                else:
                    st.caption("🔒 **Read-only connection**")
        else:
            st.session_state.selected_connection_id = None
            if st.session_state.auth_token:
                st.caption("No registered user connections. Querying default server database.")
            else:
                st.caption("Default server database (log in to manage custom connections).")

    # 4. Session Management
    with st.container(border=True):
        st.subheader("💬 Session Memory")
        st.caption(f"Session ID: `{st.session_state.session_id[:8]}...`")
        if st.button("🔄 Reset Memory & New Chat", use_container_width=True):
            try:
                httpx.delete(f"{API_BASE_URL}/sessions/{st.session_state.session_id}", timeout=3.0)
            except Exception:
                pass
            st.session_state.session_id = str(uuid.uuid4())
            st.session_state.messages = []
            st.rerun()

    # 5. Collapsible Sample Questions Expander
    with st.expander("💡 Sample Questions", expanded=False):
        sample_queries = [
            "Which customers are located in Germany?",
            "Now just show me those from Berlin",
            "What is the total revenue by product category?",
            "What are the top 5 most expensive products?",
            "How many orders were placed in 2023?",
        ]
        for q in sample_queries:
            if st.button(q, use_container_width=True):
                st.session_state["preset_query"] = q
                st.rerun()

    st.divider()
    st.caption("⚡ **Architecture:** FastAPI • Multi-LLM Router • AST Guardrails • Multi-Tenant BYODB")


def render_assistant_message(msg: dict):
    """Renders a structured assistant response including answer, metrics badge, reasoning trace, data table, charts, or write preview."""
    # Write Operation Message
    if msg.get("type") == "write_preview":
        operation = msg.get("operation", "WRITE").upper()
        status = msg.get("status", "pending")

        if status == "pending":
            st.warning(f"✍️ **Data Modification Plan Generated ({operation})** — Review preview below before executing.")
        elif status == "confirmed":
            est_flag = " (estimate)" if msg.get("affected_rows_is_estimate") else ""
            st.success(
                f"✅ **Write Committed Successfully** | Operation: `{operation}` | "
                f"Affected Rows: **{msg.get('actual_affected_rows', 0)}{est_flag}** | "
                f"Timestamp: `{msg.get('confirmed_at', '')}`\n\n"
                f"🔒 *Transaction committed and recorded in immutable audit log.*"
            )
        elif status == "discarded":
            st.info(f"ℹ️ **Write Operation Discarded** ({operation}). No database changes were applied.")
        elif status == "failed":
            st.error(f"❌ **Write Execution Failed**: {msg.get('confirm_error', 'Unknown transaction failure')}")

        # Reasoning Plan & Generated SQL
        with st.expander("🔍 Mutation Plan & Validated SQL", expanded=(status == "pending")):
            if msg.get("reasoning_plan"):
                st.markdown("**Reasoning Plan (Chain-of-Thought):**")
                st.info(msg["reasoning_plan"])
            if msg.get("sql_query"):
                st.markdown("**Validated Mutating SQL:**")
                st.code(msg["sql_query"], language="sql")

        # Affected Rows Estimate Badge
        est_count = msg.get("affected_count_estimate", 0)
        st.caption(f"📊 **Estimated Affected Rows:** `{est_count}` | ⏳ Token expires in {msg.get('expires_in_seconds', 300)}s")

        # Preview Dataframe
        preview_rows = msg.get("preview_rows", [])
        if preview_rows:
            st.markdown("##### 🔍 Preview — nothing has been changed yet")
            df = pd.DataFrame(preview_rows)
            st.dataframe(df, use_container_width=True)
        else:
            st.caption("No tabular preview rows returned for this statement.")

        # Actions for Pending Write
        if status == "pending":
            st.divider()
            col1, col2 = st.columns([2, 1])
            preview_token = msg.get("preview_token", "")
            with col1:
                if st.button("🚀 Confirm & Execute Write", key=f"btn_confirm_{preview_token}", type="primary", use_container_width=True):
                    with st.spinner("Executing transaction and logging audit trail..."):
                        conf_res, conf_err = confirm_write_in_backend(
                            preview_token=preview_token,
                            auth_token=st.session_state.auth_token,
                        )
                    if conf_err:
                        msg["status"] = "failed"
                        msg["confirm_error"] = conf_err
                    else:
                        msg["status"] = "confirmed"
                        msg["actual_affected_rows"] = conf_res.get("affected_rows", 0)
                        msg["affected_rows_is_estimate"] = conf_res.get("affected_rows_is_estimate", False)
                        msg["confirmed_at"] = conf_res.get("timestamp", "")
                    st.rerun()
            with col2:
                if st.button("❌ Discard", key=f"btn_discard_{preview_token}", use_container_width=True):
                    msg["status"] = "discarded"
                    st.rerun()
        return

    # Primary synthesized answer for read path
    if msg.get("answer"):
        st.markdown(msg["answer"])

    # Attempt / Retry Badge
    attempts = msg.get("sql_attempts", [])
    num_attempts = len(attempts)
    if num_attempts > 0:
        if num_attempts == 1:
            st.caption(f"🎯 **Resolved on 1st attempt** | ⏱️ {msg.get('total_latency_ms', 0):.0f} ms | 🪙 {msg.get('total_tokens_used', 0)} tokens (${msg.get('estimated_cost_usd', 0):.5f})")
        else:
            st.caption(f"🔄 **Resolved in {num_attempts} attempts ({num_attempts - 1} self-corrections)** | ⏱️ {msg.get('total_latency_ms', 0):.0f} ms | 🪙 {msg.get('total_tokens_used', 0)} tokens")

        # Thought Process / Reasoning Trace Expander
        with st.expander("🔍 Thought Process & Agent Reasoning Trace", expanded=False):
            for att in attempts:
                att_num = att.get("attempt", 1)
                st.markdown(f"#### 🧭 Attempt #{att_num}")
                
                if att.get("reasoning_plan"):
                    st.markdown("**Reasoning Plan (Chain-of-Thought):**")
                    st.info(att["reasoning_plan"])

                if att.get("sql_query"):
                    st.markdown("**Generated SQL Query:**")
                    st.code(att["sql_query"], language="sql")

                # AST & Execution Status
                if att.get("success"):
                    st.success(f"✓ Validated & Executed Successfully ({att.get('row_count', 0)} rows returned)")
                else:
                    st.error(f"✗ Execution Error / Failed Check: {att.get('error', 'Unknown failure')}")
                
                st.divider()

            if msg.get("final_sql"):
                st.markdown("#### ✅ Final Verified SQL")
                st.code(msg["final_sql"], language="sql")

    # Tabular Dataframe Output
    rows = msg.get("rows", [])
    if rows:
        st.markdown("##### 📋 Data Results")
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)

        # Auto-Charting for numeric results (1-2 dimensions)
        numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
        non_numeric_cols = df.select_dtypes(exclude=["number"]).columns.tolist()

        if numeric_cols and len(df) > 1 and len(df) <= 50:
            if non_numeric_cols:
                dim_col = non_numeric_cols[0]
                metric_col = numeric_cols[0]
                st.markdown(f"##### 📊 Visual: `{metric_col}` by `{dim_col}`")
                try:
                    chart_df = df.set_index(dim_col)[[metric_col]]
                    st.bar_chart(chart_df)
                except Exception:
                    pass
            elif len(numeric_cols) >= 1:
                st.markdown(f"##### 📊 Trend: `{numeric_cols[0]}`")
                try:
                    st.line_chart(df[numeric_cols[0]])
                except Exception:
                    pass


# --- Main Chat UI ---
st.title("🤖 Enterprise Text-to-SQL Agent")
st.markdown("##### *FastAPI + Multi-LLM Fallback Router + AST-Guarded SQL Execution*")
st.caption("Natural language SQL engine with AST safety guardrails, schema grounding, dialect enforcement, and multi-turn memory.")

# Mode Toggle (Read-Only Query vs Data Modification)
write_mode = st.toggle(
    "✍️ Data Modification Mode (INSERT / UPDATE / DELETE)",
    value=False,
    help="When enabled, queries will be routed to the safe write path with a preview dry-run and explicit confirmation gate.",
)

if write_mode:
    st.warning("⚠️ **Data Modification Mode Active**: Queries will be planned as mutating SQL operations. A preview will be shown for confirmation before any database changes occur.", icon="⚠️")

# Render Chat History
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["content"])
        else:
            render_assistant_message(msg)


# Handle Chat Input (or Preset query)
chat_placeholder = "Enter data modification request (e.g. 'Update stock to 50 for product 1')..." if write_mode else "Ask any question about your database (e.g. 'Show revenue by category')..."
user_input = st.chat_input(chat_placeholder)
if "preset_query" in st.session_state:
    user_input = st.session_state.pop("preset_query")

if user_input:
    # 1. Display user message
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # 2. Query Agent Backend with spinner
    with st.chat_message("assistant"):
        if write_mode:
            with st.spinner("Analyzing schema, planning safe write operation, and generating preview..."):
                response_data, error_msg = send_write_query_to_backend(
                    question=user_input,
                    session_id=st.session_state.session_id,
                    connection_id=st.session_state.selected_connection_id,
                    auth_token=st.session_state.auth_token,
                )

            if error_msg:
                st.error(error_msg)
                st.session_state.messages.append({
                    "role": "assistant",
                    "answer": f"Write Error: {error_msg}",
                    "sql_attempts": [],
                    "final_sql": None,
                    "rows": [],
                    "columns": [],
                })
            elif response_data:
                write_msg = {
                    "role": "assistant",
                    "type": "write_preview",
                    "status": "pending",
                    "preview_token": response_data.get("preview_token"),
                    "operation": response_data.get("operation", "WRITE"),
                    "sql_query": response_data.get("sql_query"),
                    "reasoning_plan": response_data.get("reasoning_plan"),
                    "preview_rows": response_data.get("preview_rows", []),
                    "affected_count_estimate": response_data.get("affected_count_estimate", 0),
                    "expires_in_seconds": response_data.get("expires_in_seconds", 300),
                }
                render_assistant_message(write_msg)
                st.session_state.messages.append(write_msg)
        else:
            with st.spinner("Analyzing schema, planning query, and executing safely..."):
                response_data, error_msg = send_query_to_backend(
                    question=user_input,
                    session_id=st.session_state.session_id,
                    connection_id=st.session_state.selected_connection_id,
                    auth_token=st.session_state.auth_token,
                )

            if error_msg:
                st.error(error_msg)
                st.session_state.messages.append({
                    "role": "assistant",
                    "answer": f"Error: {error_msg}",
                    "sql_attempts": [],
                    "final_sql": None,
                    "rows": [],
                    "columns": [],
                })
            elif response_data:
                assistant_msg = {
                    "role": "assistant",
                    "type": "read",
                    "answer": response_data["answer"],
                    "sql_attempts": response_data.get("sql_attempts", []),
                    "final_sql": response_data.get("final_sql"),
                    "rows": response_data.get("rows", []),
                    "columns": response_data.get("columns", []),
                    "total_tokens_used": response_data.get("total_tokens_used", 0),
                    "estimated_cost_usd": response_data.get("estimated_cost_usd", 0.0),
                    "total_latency_ms": response_data.get("total_latency_ms", 0.0),
                }
                render_assistant_message(assistant_msg)
                st.session_state.messages.append(assistant_msg)
