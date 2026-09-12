import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import text
from app.config import settings
from app.db import readonly_engine
from app.llm import llm_client

logger = logging.getLogger(__name__)

CACHE_DIR = "vector_cache"
SCHEMA_INDEX_PATH = os.path.join(CACHE_DIR, "schema_index.json")
GOLDEN_INDEX_PATH = os.path.join(CACHE_DIR, "golden_index.json")
VALUE_HINTS_PATH = os.path.join(CACHE_DIR, "value_hints.json")
GOLDEN_QUERIES_FILE = "data/golden_queries.json"

# Default fallback table schema with sample value hints
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
        "ddl": "Table: order_items (\n    order_id INTEGER REFERENCES orders(order_id),\n    product_id INTEGER REFERENCES products(product_id),\n    unit_price NUMERIC(10, 2),\n    quantity INTEGER,\n    discount NUMERIC(4, 2) (sample values: 0.0, 0.05, 0.1, 0.15, 0.2),\n    PRIMARY KEY (order_id, product_id)\n)",
    },
}


def _tokenize(text_str: str) -> List[str]:
    """Extracts lowercase alphanumeric tokens and bigrams for vector weighting."""
    tokens = re.findall(r"\b[a-z0-9_]+\b", text_str.lower())
    bigrams = [f"{tokens[i]}_{tokens[i+1]}" for i in range(len(tokens) - 1)]
    return tokens + bigrams


def compute_vector(text_str: str) -> Dict[str, float]:
    """Computes a normalized sparse term-frequency vector representation."""
    tokens = _tokenize(text_str)
    if not tokens:
        return {}
    counts: Dict[str, float] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0.0) + 1.0

    # L2 normalize
    norm = math.sqrt(sum(v * v for v in counts.values()))
    if norm == 0.0:
        return {}
    return {k: v / norm for k, v in counts.items()}


def cosine_similarity(vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
    """Computes cosine similarity between two normalized sparse vectors."""
    if not vec_a or not vec_b:
        return 0.0
    common_keys = set(vec_a.keys()) & set(vec_b.keys())
    return sum(vec_a[k] * vec_b[k] for k in common_keys)


class RetrievalIndex:
    """Manages vector indexing and retrieval for schema-linking and golden queries."""

    def __init__(self):
        self.schema_index: Dict[str, dict] = {}
        self.golden_index: List[dict] = []
        self.value_hints: Dict[str, Dict[str, List[Any]]] = {}
        self._load_cached_indexes()

    def _load_cached_indexes(self):
        """Loads indexes from disk cache if present, otherwise defaults."""
        os.makedirs(CACHE_DIR, exist_ok=True)
        if os.path.exists(VALUE_HINTS_PATH):
            try:
                with open(VALUE_HINTS_PATH, "r", encoding="utf-8") as f:
                    self.value_hints = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load value hints from {VALUE_HINTS_PATH}: {e}")

        if os.path.exists(SCHEMA_INDEX_PATH):
            try:
                with open(SCHEMA_INDEX_PATH, "r", encoding="utf-8") as f:
                    self.schema_index = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load schema index from {SCHEMA_INDEX_PATH}: {e}")
        else:
            # Populate default schema index
            self._build_default_schema_index()

        if os.path.exists(GOLDEN_INDEX_PATH):
            try:
                with open(GOLDEN_INDEX_PATH, "r", encoding="utf-8") as f:
                    self.golden_index = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load golden query index from {GOLDEN_INDEX_PATH}: {e}")
        else:
            self._build_default_golden_index()

    def _build_default_schema_index(self):
        """Initializes schema index from DEFAULT_TABLE_SCHEMAS."""
        self.schema_index = {}
        for table_name, data in DEFAULT_TABLE_SCHEMAS.items():
            combined_text = f"{table_name} {data['description']} {data['ddl']}"
            vec = compute_vector(combined_text)
            self.schema_index[table_name] = {
                "table_name": table_name,
                "description": data["description"],
                "ddl": data["ddl"],
                "vector": vec,
            }

    def _build_default_golden_index(self):
        """Initializes golden query index from data/golden_queries.json."""
        self.golden_index = []
        if os.path.exists(GOLDEN_QUERIES_FILE):
            try:
                with open(GOLDEN_QUERIES_FILE, "r", encoding="utf-8") as f:
                    queries = json.load(f)
                for item in queries:
                    vec = compute_vector(item["question"] + " " + item.get("reasoning", ""))
                    self.golden_index.append({
                        "question": item["question"],
                        "sql": item["sql"],
                        "reasoning": item.get("reasoning", ""),
                        "vector": vec,
                    })
            except Exception as e:
                logger.warning(f"Failed to build golden index: {e}")

    async def build_schema_index_from_db(self) -> Dict[str, dict]:
        """Extracts schema from live DB, profiles value hints, generates table descriptions, and indexes them."""
        os.makedirs(CACHE_DIR, exist_ok=True)
        schema_data: Dict[str, dict] = {}

        try:
            async with readonly_engine.connect() as conn:
                # Query columns
                query = text("""
                    SELECT 
                        t.table_name, 
                        c.column_name, 
                        c.data_type,
                        c.is_nullable
                    FROM information_schema.tables t
                    JOIN information_schema.columns c ON t.table_name = c.table_name
                    WHERE t.table_schema = 'public'
                    ORDER BY t.table_name, c.ordinal_position;
                """)
                rows = (await conn.execute(query)).fetchall()
                if not rows:
                    logger.info("No tables found in DB, using default schema index.")
                    self._build_default_schema_index()
                    return self.schema_index

                tables_cols: Dict[str, List[str]] = {}
                for table_name, col_name, data_type, _ in rows:
                    hint_str = ""
                    if table_name in self.value_hints and col_name in self.value_hints[table_name]:
                        samples = self.value_hints[table_name][col_name]
                        formatted_samples = ", ".join([f"'{s}'" if isinstance(s, str) else str(s) for s in samples[:8]])
                        hint_str = f" (sample values: {formatted_samples})"
                    tables_cols.setdefault(table_name, []).append(f"    {col_name} {data_type}{hint_str}")

                for t_name, col_lines in tables_cols.items():
                    ddl = f"Table: {t_name} (\n" + ",\n".join(col_lines) + "\n)"
                    desc = DEFAULT_TABLE_SCHEMAS.get(t_name, {}).get("description", f"Table containing {t_name} records.")
                    combined_text = f"{t_name} {desc} {ddl}"
                    vec = compute_vector(combined_text)
                    schema_data[t_name] = {
                        "table_name": t_name,
                        "description": desc,
                        "ddl": ddl,
                        "vector": vec,
                    }

                self.schema_index = schema_data
                with open(SCHEMA_INDEX_PATH, "w", encoding="utf-8") as f:
                    json.dump(schema_data, f, indent=2)

        except Exception as e:
            logger.warning(f"Could not connect to DB for schema indexing: {e}. Using defaults.")
            self._build_default_schema_index()

        return self.schema_index

    def build_golden_index(self) -> List[dict]:
        """Indexes data/golden_queries.json and writes to cache."""
        self._build_default_golden_index()
        with open(GOLDEN_INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(self.golden_index, f, indent=2)
        return self.golden_index

    def relevant_schema(self, question: str, top_k: int = 6) -> str:
        """Layer 1 + Layer 2: Retrieves the top_k most relevant tables and their columns with value hints."""
        if not self.schema_index:
            self._build_default_schema_index()

        q_vec = compute_vector(question)
        scores: List[Tuple[float, str, dict]] = []

        for table_name, data in self.schema_index.items():
            sim = cosine_similarity(q_vec, data.get("vector", {}))
            
            # Boost score if table name or singular form is explicitly in question text
            singular_name = table_name.rstrip("s")
            q_lower = question.lower()
            if table_name in q_lower or (len(singular_name) > 3 and singular_name in q_lower):
                sim += 1.5

            scores.append((sim, table_name, data))

        scores.sort(key=lambda x: x[0], reverse=True)
        top_tables = scores[:top_k]

        # Return concatenated DDL definitions with value hints
        schema_blocks = [item[2]["ddl"] for item in top_tables]
        return "\n\n".join(schema_blocks)

    def retrieve_golden_queries(self, question: str, top_k: int = 2) -> List[dict]:
        """Layer 3: Retrieves top_k most similar golden queries as few-shot examples."""
        if not self.golden_index:
            self._build_default_golden_index()

        q_vec = compute_vector(question)
        scores: List[Tuple[float, dict]] = []

        for item in self.golden_index:
            sim = cosine_similarity(q_vec, item.get("vector", {}))
            scores.append((sim, item))

        scores.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scores[:top_k]]


# Global retrieval instance
retrieval_index = RetrievalIndex()


def relevant_schema(question: str, top_k: int = 6) -> str:
    """Helper function to retrieve relevant schema blocks."""
    return retrieval_index.relevant_schema(question, top_k=top_k)


def retrieve_golden_queries(question: str, top_k: int = 2) -> List[dict]:
    """Helper function to retrieve golden few-shot query examples."""
    return retrieval_index.retrieve_golden_queries(question, top_k=top_k)
