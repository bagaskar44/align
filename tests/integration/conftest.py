"""Shared database/API scenario fixtures for integration suites."""

from __future__ import annotations

import sys
from pathlib import Path

INTEGRATION_DIR = str(Path(__file__).parent)
if INTEGRATION_DIR not in sys.path:
    sys.path.insert(0, INTEGRATION_DIR)

from test_b4_review_release import _scenario as b4_scenario  # noqa: E402,F401
