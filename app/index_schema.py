"""
CLI command to rebuild all three grounding indexes (Schema, Value Hints, Golden Queries) in one pass.
Usage:
    python -m app.index_schema
"""

import asyncio
import os
import sys
from app.retrieval import (
    CACHE_DIR,
    retrieval_index,
)
from scripts.profile_columns import profile_table_columns, save_value_hints


async def rebuild_all_indexes(verbose: bool = True):
    if verbose:
        print("=" * 60)
        print("Rebuilding Grounding Indexes (Schema, Value Hints, Golden Queries)")
        print("=" * 60)

    os.makedirs(CACHE_DIR, exist_ok=True)

    # 1. Profile low-cardinality column values
    if verbose:
        print("\n[1/3] Profiling low-cardinality column values from database...")
    hints = await profile_table_columns(max_cardinality=30)
    save_value_hints(hints, output_path=os.path.join(CACHE_DIR, "value_hints.json"))
    retrieval_index.value_hints = hints
    if verbose:
        print(f"  [OK] Value hints generated for {len(hints)} tables.")

    # 2. Extract and vector index table schemas
    if verbose:
        print("\n[2/3] Extracting and indexing table schemas...")
    schema_index = await retrieval_index.build_schema_index_from_db()
    if verbose:
        print(f"  [OK] Schema index built with {len(schema_index)} tables.")

    # 3. Index golden query few-shot examples
    if verbose:
        print("\n[3/3] Indexing golden query few-shot examples...")
    golden_index = retrieval_index.build_golden_index()
    if verbose:
        print(f"  [OK] Golden index built with {len(golden_index)} few-shot queries.")

    if verbose:
        print("\n" + "=" * 60)
        print("All 3 retrieval indexes built and saved successfully to vector_cache/!")
        print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(rebuild_all_indexes(verbose=True))
    except Exception as e:
        print(f"Error rebuilding indexes: {e}", file=sys.stderr)
        sys.exit(1)
