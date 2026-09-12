import os
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


def check_backend_health():
    try:
        resp = httpx.get(f"{API_BASE_URL}/health", timeout=3.0)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def send_query_to_backend(question: str, session_id: str):
    try:
        payload = {"question": question, "session_id": session_id}
        resp = httpx.post(f"{API_BASE_URL}/ask", json=payload, timeout=60.0)
        if resp.status_code == 200:
            return resp.json(), None
        elif resp.status_code == 429:
            return None, "Rate limit exceeded (HTTP 429). Please wait a moment before sending another query."
        else:
            return None, f"Backend Error ({resp.status_code}): {resp.text}"
    except httpx.ConnectError:
        return None, "Could not connect to FastAPI backend at http://localhost:8000. Please make sure the server is running (`uvicorn app.main:app --port 8000`)."
    except Exception as e:
        return None, f"Unexpected error: {str(e)}"


# --- Sidebar ---
with st.sidebar:
    st.title("⚙️ Agent Settings")
    
    # Backend Health Status
    health = check_backend_health()
    if health:
        st.success(f"🟢 Backend Online ({health.get('dialect', 'postgres').upper()})")
        st.caption(f"Model: `{health.get('llm_model', 'gpt-4o')}` | Dialect: `{health.get('dialect')}`")
    else:
        st.error("🔴 Backend Offline (http://localhost:8000)")
        st.caption("Start with: `uvicorn app.main:app --reload --port 8000`")

    st.divider()

    # Session Management
    st.subheader("💬 Session Management")
    st.code(st.session_state.session_id, language="text")
    if st.button("🔄 New Conversation / Reset Memory", use_container_width=True):
        try:
            httpx.delete(f"{API_BASE_URL}/sessions/{st.session_state.session_id}", timeout=3.0)
        except Exception:
            pass
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.subheader("💡 Sample Questions")
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


# --- Main Chat UI ---
st.title("🤖 Enterprise Text-to-SQL Agent")
st.caption("Natural language SQL engine with AST safety guardrails, schema grounding, dialect enforcement, and multi-turn memory.")

# Render Chat History
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["content"])
        else:
            # Primary synthesized answer
            st.markdown(msg["answer"])

            # Attempt / Retry Badge
            attempts = msg.get("sql_attempts", [])
            num_attempts = len(attempts)
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


# Handle Chat Input (or Preset query)
user_input = st.chat_input("Ask any question about your database (e.g. 'Show revenue by category')...")
if "preset_query" in st.session_state:
    user_input = st.session_state.pop("preset_query")

if user_input:
    # 1. Display user message
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # 2. Query Agent Backend with spinner
    with st.chat_message("assistant"):
        with st.spinner("Analyzing schema, planning query, and executing safely..."):
            response_data, error_msg = send_query_to_backend(user_input, st.session_state.session_id)

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
            # Display synthesized answer
            st.markdown(response_data["answer"])

            # Attempt Badge
            attempts = response_data.get("sql_attempts", [])
            num_attempts = len(attempts)
            if num_attempts == 1:
                st.caption(f"🎯 **Resolved on 1st attempt** | ⏱️ {response_data.get('total_latency_ms', 0):.0f} ms | 🪙 {response_data.get('total_tokens_used', 0)} tokens (${response_data.get('estimated_cost_usd', 0):.5f})")
            else:
                st.caption(f"🔄 **Resolved in {num_attempts} attempts ({num_attempts - 1} self-corrections)** | ⏱️ {response_data.get('total_latency_ms', 0):.0f} ms | 🪙 {response_data.get('total_tokens_used', 0)} tokens")

            # Thought Process Expander
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
                    if att.get("success"):
                        st.success(f"✓ Validated & Executed Successfully ({att.get('row_count', 0)} rows returned)")
                    else:
                        st.error(f"✗ Execution Error / Failed Check: {att.get('error', 'Unknown failure')}")
                    st.divider()

                if response_data.get("final_sql"):
                    st.markdown("#### ✅ Final Verified SQL")
                    st.code(response_data["final_sql"], language="sql")

            # Tabular Output & Charting
            rows = response_data.get("rows", [])
            if rows:
                st.markdown("##### 📋 Data Results")
                df = pd.DataFrame(rows)
                st.dataframe(df, use_container_width=True)

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

            # Store in session state history
            st.session_state.messages.append({
                "role": "assistant",
                "answer": response_data["answer"],
                "sql_attempts": response_data.get("sql_attempts", []),
                "final_sql": response_data.get("final_sql"),
                "rows": response_data.get("rows", []),
                "columns": response_data.get("columns", []),
                "total_tokens_used": response_data.get("total_tokens_used", 0),
                "estimated_cost_usd": response_data.get("estimated_cost_usd", 0.0),
                "total_latency_ms": response_data.get("total_latency_ms", 0.0),
            })
