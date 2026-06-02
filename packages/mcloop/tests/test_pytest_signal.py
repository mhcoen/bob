"""Tests for the pytest outcome-class parser."""

from mcloop.pytest_signal import NO_SIGNAL, PytestSignal, parse_pytest_signal


def test_normal_pass_summary():
    stdout = (
        "============================= test session starts =============================\n"
        "collected 2474 items\n"
        "\n"
        "tests/test_a.py ...\n"
        "\n"
        "======================= 2474 passed in 340.94s (0:05:40) ======================="
    )
    sig = parse_pytest_signal(stdout, exit_code=0)
    assert sig == PytestSignal(
        collected=2474,
        passed=2474,
        failed=0,
        skipped=0,
        deselected=0,
        xfailed=0,
        xpassed=0,
        exit_code=0,
    )


def test_bare_non_q_summary():
    """The bare summary form without ``===`` framing parses too."""
    sig = parse_pytest_signal("2474 passed in 340.94s", exit_code=0)
    assert sig is not None
    assert sig.passed == 2474
    # No explicit "collected" line, so it is inferred from the outcomes.
    assert sig.collected == 2474
    assert sig.exit_code == 0


def test_all_skipped_summary():
    stdout = "collected 3 items\n========== 3 skipped in 0.12s =========="
    sig = parse_pytest_signal(stdout, exit_code=0)
    assert sig == PytestSignal(
        collected=3,
        passed=0,
        failed=0,
        skipped=3,
        deselected=0,
        xfailed=0,
        xpassed=0,
        exit_code=0,
    )


def test_all_deselected_summary():
    stdout = "collected 10 items / 10 deselected\n========== 10 deselected in 0.03s =========="
    sig = parse_pytest_signal(stdout, exit_code=5)
    assert sig == PytestSignal(
        collected=10,
        passed=0,
        failed=0,
        skipped=0,
        deselected=10,
        xfailed=0,
        xpassed=0,
        exit_code=5,
    )


def test_zero_collected_exit_5():
    stdout = "collected 0 items\n========== no tests ran in 0.01s =========="
    sig = parse_pytest_signal(stdout, exit_code=5)
    assert sig == PytestSignal(
        collected=0,
        passed=0,
        failed=0,
        skipped=0,
        deselected=0,
        xfailed=0,
        xpassed=0,
        exit_code=5,
    )


def test_unparseable_blob_returns_sentinel():
    blob = "Traceback (most recent call last):\nImportError: boom\nrandom noise"
    assert parse_pytest_signal(blob, exit_code=1) is NO_SIGNAL


def test_mixed_summary_does_not_confuse_xpassed_with_passed():
    stdout = (
        "collected 9 items\n"
        "===== 1 failed, 2 passed, 3 skipped, 1 xfailed, 1 xpassed, "
        "1 deselected in 1.23s ====="
    )
    sig = parse_pytest_signal(stdout, exit_code=1)
    assert sig == PytestSignal(
        collected=9,
        passed=2,
        failed=1,
        skipped=3,
        deselected=1,
        xfailed=1,
        xpassed=1,
        exit_code=1,
    )
