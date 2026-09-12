"""
Script to profile low-cardinality columns in the database and generate value hints.
This enables the Text-to-SQL agent to ground business terms (e.g. 'discontinued', 'active', 'Germany')
to actual values present in the database.
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text
from app.db import readonly_engine


async def profile_table_columns(
    max_cardinality: int = 30,
) -> Dict[str, Dict[str, List[Any]]]:
    """Inspects all tables in schema 'public' and identifies low-cardinality column values."""
    results: Dict[str, Dict[str, List[Any]]] = {}

    try:
        async with readonly_engine.connect() as conn:
            # Query all tables and columns in schema public
            query = text("""
                SELECT 
                    table_name, 
                    column_name, 
                    data_type,
                    character_maximum_length
                FROM information_schema.columns 
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position;
            """)
            col_rows = (await conn.execute(query)).fetchall()

            for table_name, col_name, data_type, max_len in col_rows:
                # Target low-cardinality candidate types (text, varchar, boolean, char)
                target_types = ("character varying", "varchar", "text", "character", "char", "boolean")
                is_candidate = any(t in data_type.lower() for t in target_types)

                # Skip primary key ID fields that are clearly high cardinality if length > 50
                if is_candidate:
                    try:
                        # Count distinct values
                        count_sql = text(f'SELECT COUNT(DISTINCT "{col_name}") FROM "{table_name}";')
                        distinct_count = (await conn.execute(count_sql)).scalar() or 0

                        if 0 < distinct_count <= max_cardinality:
                            # Fetch distinct values
                            val_sql = text(f'SELECT DISTINCT "{col_name}" FROM "{table_name}" WHERE "{col_name}" IS NOT NULL ORDER BY "{col_name}" LIMIT {max_cardinality};')
                            distinct_vals = [r[0] for r in (await conn.execute(val_sql)).fetchall()]

                            results.setdefault(table_name, {})[col_name] = distinct_vals
                    except Exception as e:
                        # Continue if query fails on a specific column
                        continue

    except Exception as e:
        print(f"Warning: Error profiling columns from database: {e}", file=sys.stderr)

    return results


def save_value_hints(hints: Dict[str, Dict[str, List[Any]]], output_path: str = "vector_cache/value_hints.json"):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(hints, f, indent=2, default=str)
    print(f"Saved value hints for {len(hints)} tables to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Profile low-cardinality column values.")
    parser.add_argument("--output", default="vector_cache/value_hints.json", help="Path to save value hints JSON")
    parser.add_argument("--max-cardinality", type=int, default=30, help="Maximum distinct values to consider low-cardinality")
    args = parser.parse_args()

    print("Profiling column values across database...")
    hints = asyncio.run(profile_table_columns(max_cardinality=args.max_cardinality))
    save_value_hints(hints, output_path=args.output)
    print("Done profiling.")
