import pytest
from scripts.run_eval import compare_result_sets, normalize_result_rows, normalize_value


def test_normalize_value():
    assert normalize_value(None) == "NULL"
    assert normalize_value(42.500) == 42.5
    assert normalize_value(True) == "true"
    assert normalize_value("  Chai  ") == "Chai"


def test_compare_result_sets_equivalent_different_order():
    # Order of rows and columns differ, but multisets match
    gold_rows = [("Chai", 18.0), ("Chang", 19.0)]
    agent_rows = [(19.0, "Chang"), (18.0, "Chai")]
    assert compare_result_sets(agent_rows, gold_rows) is True


def test_compare_result_sets_mismatch():
    gold_rows = [("Chai", 18.0)]
    agent_rows = [("Aniseed Syrup", 10.0)]
    assert compare_result_sets(agent_rows, gold_rows) is False


def test_compare_result_sets_none_handling():
    assert compare_result_sets(None, [("Chai",)]) is False
    assert compare_result_sets([("Chai",)], None) is False
    assert compare_result_sets(None, None) is False
