"""Tests for required councilor count validation."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.units.registration_cycle_service import required_councilor_count  # noqa: E402


def test_required_councilor_count_tiers():
    assert required_councilor_count(1) == 1
    assert required_councilor_count(25) == 1
    assert required_councilor_count(26) == 2
    assert required_councilor_count(50) == 2
    assert required_councilor_count(51) == 3
    assert required_councilor_count(75) == 3
    assert required_councilor_count(76) == 4
    assert required_councilor_count(100) == 4
    assert required_councilor_count(101) == 5
