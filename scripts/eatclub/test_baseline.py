import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))

import baseline  # noqa: E402


def test_dow_baseline_means_and_window():
    rows = [
        {"date": "2026-05-08", "v": "2000"},   # Fri, in window
        {"date": "2026-05-15", "v": "3000"},   # Fri, in window  -> mean 2500
        {"date": "2026-05-09", "v": "1000"},   # Sat
        {"date": "2026-07-10", "v": "9999"},   # Fri, OUTSIDE window -> ignored
        {"date": "2026-05-22", "v": ""},        # Fri, blank -> ignored
    ]
    b = baseline.dow_baseline(rows, "v", "2026-05-06", "2026-06-30")
    assert b["Fri"] == Decimal("2500.00")
    assert b["Sat"] == Decimal("1000.00")
    assert "Mon" not in b            # no data -> omitted
