import pytest
from dailypicks.odds import clv_raw, devig_power, devig_proportional, devig_shin, expected_value


@pytest.mark.parametrize("fn", [devig_proportional, devig_power, devig_shin])
def test_devig_sums_to_one_and_keeps_order(fn):
    p = fn([1.61, 2.37])
    assert abs(sum(p) - 1) < 1e-9 and p[0] > p[1]
    p3 = fn([2.5, 3.4, 2.9])
    assert abs(sum(p3) - 1) < 1e-9


@pytest.mark.parametrize("fn", [devig_proportional, devig_power, devig_shin])
def test_fair_book_unchanged(fn):
    p = fn([4.0, 4.0, 2.0])
    assert all(abs(a - b) < 1e-6 for a, b in zip(p, [0.25, 0.25, 0.5]))


def test_methods_differ_for_unbalanced_books():
    o = [1.25, 4.4]
    assert devig_proportional(o) != devig_shin(o) and devig_power(o) != devig_proportional(o)


def test_value_arithmetic_examples():
    assert abs(expected_value(0.8, 0.2, 1.25) - (0.8 * 0.25 - 0.2)) < 1e-12
    assert clv_raw(1.60, 1.50) > 0      # took 1.60, closed 1.50: favourable
    assert clv_raw(1.50, 1.60) < 0      # took 1.50, closed 1.60: unfavourable
