import json
import logging
import math
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple, Union
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from app.config import settings
from app.db import readonly_engine

logger = logging.getLogger(__name__)

CACHE_BASE_DIR = "vector_cache"
CACHE_DIR = CACHE_BASE_DIR
GOLDEN_QUERIES_FILE = "data/golden_queries.json"

DEFAULT_TABLE_SCHEMAS = {
    "categories": {
        "description": "Product categories grouping items like Beverages, Condiments, Confections, Dairy Products, Seafood, etc.",
        "ddl": "Table: categories (\n    category_id INTEGER PRIMARY KEY,\n    category_name VARCHAR(100) (sample values: 'Beverages', 'Condiments', 'Confections', 'Dairy Products', 'Grains & Cereals', 'Meat & Poultry', 'Produce', 'Seafood'),\n    description TEXT\n)",
    },
    "products": {
        "description": "Product inventory details including unit prices, stock counts, category references, and discontinuation status.",
        "ddl": "Table: products (\n    product_id INTEGER PRIMARY KEY,\n    product_name VARCHAR(150),\n    category_id INTEGER REFERENCES categories(category_id),\n    unit_price NUMERIC(10, 2),\n    units_in_stock INTEGER,\n    discontinued BOOLEAN (sample values: false, true)\n)",
    },
    "customers": {
        "description": "Customer organizations, contact people, and global locations such as Germany, Mexico, UK, Sweden, France, Spain, Canada.",
        "ddl": "Table: customers (\n    customer_id VARCHAR(10) PRIMARY KEY,\n    company_name VARCHAR(150),\n    contact_name VARCHAR(100),\n    country VARCHAR(50) (sample values: 'Canada', 'France', 'Germany', 'Mexico', 'Spain', 'Sweden', 'UK'),\n    city VARCHAR(50) (sample values: 'Berlin', 'London', 'Madrid', 'Marseille', 'Mexico D.F.', 'Strasbourg', 'Tsawwassen')\n)",
    },
    "employees": {
        "description": "Company personnel, sales representatives, managers, departments, titles, and hire dates.",
        "ddl": "Table: employees (\n    employee_id INTEGER PRIMARY KEY,\n    first_name VARCHAR(50),\n    last_name VARCHAR(50),\n    title VARCHAR(100) (sample values: 'Sales Manager', 'Sales Representative', 'Vice President, Sales'),\n    hire_date DATE,\n    department VARCHAR(50) (sample values: 'Sales')\n)",
    },
    "orders": {
        "description": "Sales order headers recording customer, handling employee, order date, shipping destination country, and freight shipping costs.",
        "ddl": "Table: orders (\n    order_id INTEGER PRIMARY KEY,\n    customer_id VARCHAR(10) REFERENCES customers(customer_id),\n    employee_id INTEGER REFERENCES employees(employee_id),\n    order_date DATE,\n    ship_country VARCHAR(50) (sample values: 'Canada', 'France', 'Germany', 'Mexico', 'Spain', 'Sweden', 'UK'),\n    freight NUMERIC(10, 2)\n)",
    },
    "order_items": {
        "description": "Line items for orders linking products to orders with unit price, ordered quantity, and discount percentages.",
        "ddl": "Table: order_items (\n    order_id REFERENCES orders(order_id),\n    product_id INTEGER REFERENCES products(product_id),\n    unit_price NUMERIC(10, 2),\n    quantity INTEGER,\n    discount NUMERIC(4, 2) (sample values: 0.0, 0.05, 0.1, 0.15, 0.2),\n    PRIMARY KEY (order_id, product_id)\n)",
    },
}


# --- Keyword (Sparse TF-IDF / Token Frequency) Vector Utilities ---

def _tokenize(text_str: str) -> List[str]:
    """Extracts lowercase alphanumeric tokens and bigrams for sparse keyword vector weighting."""
    tokens = re.findall(r"\b[a-z0-9_]+\b", text_str.lower())
    bigrams = [f"{tokens[i]}_{tokens[i+1]}" for i in range(len(tokens) - 1)]
    return tokens + bigrams


def compute_keyword_vector(text_str: str) -> Dict[str, float]:
    """Computes a normalized sparse keyword vector representation (unigrams + bigrams)."""
    tokens = _tokenize(text_str)
    if not tokens:
        return {}
    counts: Dict[str, float] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0.0) + 1.0

    norm = math.sqrt(sum(v * v for v in counts.values()))
    if norm == 0.0:
        return {}
    return {k: v / norm for k, v in counts.items()}


def sparse_cosine_similarity(vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
    """Computes cosine similarity between two normalized sparse keyword vectors."""
    if not vec_a or not vec_b:
        return 0.0
    common_keys = set(vec_a.keys()) & set(vec_b.keys())
    return sum(vec_a[k] * vec_b[k] for k in common_keys)


# --- Dense OpenAI Embeddings Utilities ---

def compute_openai_embedding(text_str: str) -> Optional[List[float]]:
    """Generates dense vector embeddings using OpenAI API if configured."""
    if not settings.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.embeddings.create(
            input=text_str,
            model=settings.OPENAI_EMBEDDING_MODEL,
        )
        return response.data[0].embedding
    except Exception as e:
        logger.warning(f"OpenAI embedding generation failed: {e}. Falling back to keyword vector.")
        return None


def dense_cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Computes cosine similarity between two dense float vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot_product / (norm_a * norm_b)


class RetrievalIndex:
    """
    Manages structural schema-linking, value hinting, and golden query retrieval.
    Scoped per connection_id (or global default).
    """

    def __init__(self, connection_id: Optional[str] = None):
        self.connection_id = connection_id
        if connection_id:
            self.cache_dir = os.path.join(CACHE_BASE_DIR, "connections", connection_id)
        else:
            self.cache_dir = CACHE_BASE_DIR

        self.schema_index_path = os.path.join(self.cache_dir, "schema_index.json")
        self.golden_index_path = os.path.join(self.cache_dir, "golden_index.json")
        self.value_hints_path = os.path.join(self.cache_dir, "value_hints.json")

        self.schema_index: Dict[str, dict] = {}
        self.golden_index: List[dict] = []
        self.value_hints: Dict[str, Dict[str, List[Any]]] = {}
        self._load_cached_indexes()

    def _load_cached_indexes(self):
        """Loads index metadata from connection-specific cache or initializes defaults."""
        os.makedirs(self.cache_dir, exist_ok=True)
        if os.path.exists(self.value_hints_path):
            try:
                with open(self.value_hints_path, "r", encoding="utf-8") as f:
                    self.value_hints = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load value hints for {self.connection_id}: {e}")

        if os.path.exists(self.schema_index_path):
            try:
                with open(self.schema_index_path, "r", encoding="utf-8") as f:
                    self.schema_index = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load schema index for {self.connection_id}: {e}")
        else:
            self._build_default_schema_index()

        if os.path.exists(self.golden_index_path):
            try:
                with open(self.golden_index_path, "r", encoding="utf-8") as f:
                    self.golden_index = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load golden index for {self.connection_id}: {e}")
        else:
            self._build_default_golden_index()

    def _build_default_schema_index(self):
        self.schema_index = {}
        for table_name, data in DEFAULT_TABLE_SCHEMAS.items():
            combined_text = f"{table_name} {data['description']} {data['ddl']}"
            kw_vec = compute_keyword_vector(combined_text)
            dense_vec = None
            if settings.EMBEDDING_BACKEND == "openai" and settings.OPENAI_API_KEY:
                dense_vec = compute_openai_embedding(combined_text)

            self.schema_index[table_name] = {
                "table_name": table_name,
                "description": data["description"],
                "ddl": data["ddl"],
                "keyword_vector": kw_vec,
                "dense_vector": dense_vec,
            }

    def _build_default_golden_index(self):
        self.golden_index = []
        if os.path.exists(GOLDEN_QUERIES_FILE):
            try:
                with open(GOLDEN_QUERIES_FILE, "r", encoding="utf-8") as f:
                    queries = json.load(f)
                for item in queries:
                    comb_text = item["question"] + " " + item.get("reasoning", "")
                    kw_vec = compute_keyword_vector(comb_text)
                    dense_vec = None
                    if settings.EMBEDDING_BACKEND == "openai" and settings.OPENAI_API_KEY:
                        dense_vec = compute_openai_embedding(comb_text)

                    self.golden_index.append({
                        "question": item["question"],
                        "sql": item["sql"],
                        "reasoning": item.get("reasoning", ""),
                        "keyword_vector": kw_vec,
                        "dense_vector": dense_vec,
                    })
            except Exception as e:
                logger.warning(f"Failed to build golden index: {e}")

    async def build_schema_index_from_engine(self, engine: AsyncEngine, dialect: str = "postgres") -> Dict[str, dict]:
        """Inspects database tables, profiles low-cardinality values, and indexes schema for this connection."""
        os.makedirs(self.cache_dir, exist_ok=True)
        schema_data: Dict[str, dict] = {}
        self.value_hints = {}

        try:
            async with engine.connect() as conn:
                if "sqlite" in dialect.lower() or "sqlite" in str(engine.url).lower():
                    # SQLite schema inspection
                    table_rows = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))).fetchall()
                    table_names = [r[0] for r in table_rows]
                    tables_cols: Dict[str, List[str]] = {}
                    for t_name in table_names:
                        col_info = (await conn.execute(text(f"PRAGMA table_info('{t_name}')"))).fetchall()
                        for c in col_info:
                            c_name = c[1]
                            c_type = c[2] or "TEXT"
                            # Profile distinct values
                            try:
                                val_rows = (await conn.execute(text(f"SELECT DISTINCT {c_name} FROM {t_name} WHERE {c_name} IS NOT NULL LIMIT 30"))).fetchall()
                                distinct_vals = [r[0] for r in val_rows]
                                if 0 < len(distinct_vals) <= 25:
                                    self.value_hints.setdefault(t_name, {})[c_name] = distinct_vals
                                    formatted = ", ".join([f"'{s}'" if isinstance(s, str) else str(s) for s in distinct_vals[:8]])
                                    hint_str = f" (sample values: {formatted})"
                                else:
                                    hint_str = ""
                            except Exception:
                                hint_str = ""
                            tables_cols.setdefault(t_name, []).append(f"    {c_name} {c_type}{hint_str}")
                else:
                    # Postgres / ANSI Information Schema
                    query = text("""
                        SELECT 
                            t.table_name, 
                            c.column_name, 
                            c.data_type
                        FROM information_schema.tables t
                        JOIN information_schema.columns c ON t.table_name = c.table_name
                        WHERE t.table_schema = 'public'
                        ORDER BY t.table_name, c.ordinal_position;
                    """)
                    rows = (await conn.execute(query)).fetchall()
                    if not rows:
                        self._build_default_schema_index()
                        return self.schema_index

                    tables_cols: Dict[str, List[str]] = {}
                    for table_name, col_name, data_type in rows:
                        # Value profiling for low-cardinality columns
                        hint_str = ""
                        try:
                            if "char" in data_type.lower() or "text" in data_type.lower() or "bool" in data_type.lower():
                                val_rows = (await conn.execute(text(f'SELECT DISTINCT "{col_name}" FROM "{table_name}" WHERE "{col_name}" IS NOT NULL LIMIT 30'))).fetchall()
                                distinct_vals = [r[0] for r in val_rows]
                                if 0 < len(distinct_vals) <= 25:
                                    self.value_hints.setdefault(table_name, {})[col_name] = distinct_vals
                                    formatted = ", ".join([f"'{s}'" if isinstance(s, str) else str(s) for s in distinct_vals[:8]])
                                    hint_str = f" (sample values: {formatted})"
                        except Exception:
                            pass
                        tables_cols.setdefault(table_name, []).append(f"    {col_name} {data_type}{hint_str}")

                for t_name, col_lines in tables_cols.items():
                    ddl = f"Table: {t_name} (\n" + ",\n".join(col_lines) + "\n)"
                    desc = DEFAULT_TABLE_SCHEMAS.get(t_name, {}).get("description", f"Table containing {t_name} records.")
                    combined_text = f"{t_name} {desc} {ddl}"
                    kw_vec = compute_keyword_vector(combined_text)
                    dense_vec = None
                    if settings.EMBEDDING_BACKEND == "openai" and settings.OPENAI_API_KEY:
                        dense_vec = compute_openai_embedding(combined_text)

                    schema_data[t_name] = {
                        "table_name": t_name,
                        "description": desc,
                        "ddl": ddl,
                        "keyword_vector": kw_vec,
                        "dense_vector": dense_vec,
                    }

                self.schema_index = schema_data
                with open(self.schema_index_path, "w", encoding="utf-8") as f:
                    json.dump(schema_data, f, indent=2)
                with open(self.value_hints_path, "w", encoding="utf-8") as f:
                    json.dump(self.value_hints, f, indent=2)

        except Exception as e:
            logger.warning(f"Could not connect to DB for schema indexing ({self.connection_id}): {e}. Using defaults.")
            self._build_default_schema_index()

        return self.schema_index

    async def build_schema_index_from_db(self) -> Dict[str, dict]:
        """Backward compatible helper using readonly_engine."""
        return await self.build_schema_index_from_engine(readonly_engine, settings.SQL_DIALECT)

    def build_golden_index(self) -> List[dict]:
        self._build_default_golden_index()
        with open(self.golden_index_path, "w", encoding="utf-8") as f:
            json.dump(self.golden_index, f, indent=2)
        return self.golden_index

    def relevant_schema(self, question: str, top_k: int = 6) -> str:
        """Retrieves top_k relevant tables/columns with sample value hints using keyword or dense embeddings."""
        if not self.schema_index:
            self._build_default_schema_index()

        use_openai = settings.EMBEDDING_BACKEND == "openai" and bool(settings.OPENAI_API_KEY)
        q_dense = compute_openai_embedding(question) if use_openai else None
        q_kw = compute_keyword_vector(question)

        scores: List[Tuple[float, str, dict]] = []

        for table_name, data in self.schema_index.items():
            if use_openai and q_dense and data.get("dense_vector"):
                sim = dense_cosine_similarity(q_dense, data["dense_vector"])
            else:
                target_vec = data.get("keyword_vector") or data.get("vector", {})
                sim = sparse_cosine_similarity(q_kw, target_vec)

            # Heuristic boosting if table name is in question
            singular_name = table_name.rstrip("s")
            q_lower = question.lower()
            if table_name in q_lower or (len(singular_name) > 3 and singular_name in q_lower):
                sim += 1.5

            scores.append((sim, table_name, data))

        scores.sort(key=lambda x: x[0], reverse=True)
        top_tables = scores[:top_k]
        schema_blocks = [item[2]["ddl"] for item in top_tables]
        return "\n\n".join(schema_blocks)

    def retrieve_golden_queries(self, question: str, top_k: int = 2) -> List[dict]:
        """Retrieves top_k most similar golden queries as few-shot examples."""
        if not self.golden_index:
            self._build_default_golden_index()

        use_openai = settings.EMBEDDING_BACKEND == "openai" and bool(settings.OPENAI_API_KEY)
        q_dense = compute_openai_embedding(question) if use_openai else None
        q_kw = compute_keyword_vector(question)

        scores: List[Tuple[float, dict]] = []

        for item in self.golden_index:
            if use_openai and q_dense and item.get("dense_vector"):
                sim = dense_cosine_similarity(q_dense, item["dense_vector"])
            else:
                target_vec = item.get("keyword_vector") or item.get("vector", {})
                sim = sparse_cosine_similarity(q_kw, target_vec)

            scores.append((sim, item))

        scores.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scores[:top_k]]


# In-memory index registry
_connection_indexes: Dict[str, RetrievalIndex] = {}
default_retrieval_index = RetrievalIndex()
retrieval_index = default_retrieval_index


def get_retrieval_index(connection_id: Optional[str] = None) -> RetrievalIndex:
    """Returns the namespaced RetrievalIndex for a connection, or the default index."""
    if not connection_id:
        return default_retrieval_index
    if connection_id not in _connection_indexes:
        _connection_indexes[connection_id] = RetrievalIndex(connection_id=connection_id)
    return _connection_indexes[connection_id]


def delete_connection_retrieval_index(connection_id: str) -> None:
    """Removes cached index from memory and disk for a deleted connection."""
    _connection_indexes.pop(connection_id, None)
    dir_path = os.path.join(CACHE_BASE_DIR, "connections", connection_id)
    if os.path.exists(dir_path):
        shutil.rmtree(dir_path, ignore_errors=True)


def relevant_schema(question: str, top_k: int = 6, connection_id: Optional[str] = None) -> str:
    return get_retrieval_index(connection_id).relevant_schema(question, top_k=top_k)


def retrieve_golden_queries(question: str, top_k: int = 2, connection_id: Optional[str] = None) -> List[dict]:
    return get_retrieval_index(connection_id).retrieve_golden_queries(question, top_k=top_k)
