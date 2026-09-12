"""
Evaluation Harness for Text-to-SQL Agent: Execution Accuracy (EX) Benchmark.
Executes both generated and gold SQL against the target database and compares result sets for equivalence.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, List, Optional, Set, Tuple
from sqlalchemy import text
from app.agent import answer_question
from app.db import readonly_engine


def normalize_value(val: Any) -> Any:
    """Normalizes database values for order-independent equivalence comparison."""
    if val is None:
        return "NULL"
    if isinstance(val, float):
        return round(val, 2)
    # Convert Decimals or numbers represented as floats
    try:
        if hasattr(val, "as_tuple"):  # Decimal
            return round(float(val), 2)
    except Exception:
        pass
    if isinstance(val, bool):
        return str(val).lower()
    return str(val).strip()


def normalize_result_rows(rows: List[tuple]) -> List[Tuple[Any, ...]]:
    """
    Normalizes a list of database row tuples:
    1. Sorts the values within each row by str representation so column order does not matter.
    2. Sorts the list of rows so row order does not matter.
    """
    normalized_rows = []
    for row in rows:
        norm_row = tuple(sorted([normalize_value(v) for v in row], key=lambda x: str(x)))
        normalized_rows.append(norm_row)
    return sorted(normalized_rows, key=lambda r: [str(x) for x in r])


async def execute_raw_sql(sql_query: str) -> Optional[List[tuple]]:
    """Executes a SQL query and returns raw row tuples."""
    try:
        async with readonly_engine.connect() as conn:
            result = await conn.execute(text(sql_query))
            if result.returns_rows:
                return result.fetchall()
            return []
    except Exception as e:
        return None


def compare_result_sets(agent_rows: Optional[List[tuple]], gold_rows: Optional[List[tuple]]) -> bool:
    """Compares two database result sets for data equivalence."""
    if agent_rows is None or gold_rows is None:
        return False
    
    norm_agent = normalize_result_rows(agent_rows)
    norm_gold = normalize_result_rows(gold_rows)

    return norm_agent == norm_gold


async def run_evaluation(
    eval_set_path: str = "tests/eval/eval_set.json",
    threshold: float = 0.7,
    max_questions: Optional[int] = None,
) -> float:
    if not os.path.exists(eval_set_path):
        print(f"Error: Evaluation set not found at {eval_set_path}", file=sys.stderr)
        sys.exit(1)

    with open(eval_set_path, "r", encoding="utf-8") as f:
        eval_items = json.load(f)

    if max_questions:
        eval_items = eval_items[:max_questions]

    total_count = len(eval_items)
    passed_count = 0
    results_summary = []

    print("=" * 80)
    print(f"Running Text-to-SQL Execution Accuracy (EX) Benchmark ({total_count} questions)")
    print("=" * 80)

    for i, item in enumerate(eval_items, 1):
        q_id = item.get("id", f"q_{i}")
        question = item["question"]
        gold_sql = item["gold_sql"]

        print(f"\n[{i}/{total_count}] Evaluating: {question}")
        start_t = time.time()

        # Execute Gold SQL first
        gold_rows = await execute_raw_sql(gold_sql)

        # Call Agent
        try:
            agent_resp = await answer_question(question=question)
            agent_sql = agent_resp.get("final_sql")
            latency_ms = (time.time() - start_t) * 1000.0
        except Exception as e:
            agent_sql = None
            latency_ms = (time.time() - start_t) * 1000.0

        if agent_sql:
            agent_rows = await execute_raw_sql(agent_sql)
        else:
            agent_rows = None

        is_passed = compare_result_sets(agent_rows, gold_rows)
        if is_passed:
            passed_count += 1
            status_str = "PASSED ✓"
        else:
            status_str = "FAILED ✗"

        print(f"  Status: {status_str} (Latency: {latency_ms:.0f} ms)")
        print(f"  Agent SQL: {agent_sql}")
        print(f"  Gold SQL:  {gold_sql}")

        results_summary.append({
            "id": q_id,
            "question": question,
            "passed": is_passed,
            "latency_ms": round(latency_ms, 2),
            "agent_sql": agent_sql,
            "gold_sql": gold_sql,
        })

    accuracy = passed_count / total_count if total_count > 0 else 0.0
    print("\n" + "=" * 80)
    print("EVALUATION BENCHMARK SUMMARY")
    print("=" * 80)
    print(f"Total Evaluated:      {total_count}")
    print(f"Passed (EX Equiv):    {passed_count}")
    print(f"Failed:               {total_count - passed_count}")
    print(f"Execution Accuracy:   {accuracy * 100:.1f}%")
    print(f"Required Threshold:   {threshold * 100:.1f}%")
    print("=" * 80)

    if accuracy < threshold:
        print(f"\n❌ Benchmark FAILED: Execution accuracy ({accuracy * 100:.1f}%) is below threshold ({threshold * 100:.1f}%).", file=sys.stderr)
        return accuracy
    else:
        print(f"\n✅ Benchmark PASSED: Execution accuracy ({accuracy * 100:.1f}%) meets threshold ({threshold * 100:.1f}%).")
        return accuracy


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Text-to-SQL Execution Accuracy (EX) benchmark.")
    parser.add_argument("--eval-set", default="tests/eval/eval_set.json", help="Path to evaluation JSON")
    parser.add_argument("--threshold", type=float, default=0.70, help="Minimum accuracy pass threshold (0.0 - 1.0)")
    parser.add_argument("--max-questions", type=int, default=None, help="Limit number of eval items to run")

    args = parser.parse_args()
    acc = asyncio.run(run_evaluation(
        eval_set_path=args.eval_set,
        threshold=args.threshold,
        max_questions=args.max_questions,
    ))
    if acc < args.threshold:
        sys.exit(1)
