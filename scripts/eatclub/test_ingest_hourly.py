"""Ingest test, seeded with a real slice of the 2026-07-17 Custom Insights pull.

Uses the display headers observed in the portal; the resolver matches by
substring so it survives Looker's prefix variations.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import ingest_hourly  # noqa: E402

# Real feed shape: leading index column (blank header), $-prefixed money,
# thousands commas. This mirrors the live 2026-07-17 CSV that first broke ingest.
SAMPLE = (
    ",Reporting Group Name,Sale Closed Hour of Day,Site Name,Sale Closed Date,"
    "Total Revenue,Gross Sale Ex Tax,Gross Sale Inc Tax\n"
    # Stowaway-proper, in window (17-20)
    "1,Cocktails - Classic,18,Stowaway Bar,2026-07-17,,$990.88,\"$1,090.00\"\n"
    "2,Gin,20,Stowaway Bar,2026-07-17,,$62.73,$69.00\n"
    # Stowaway-proper, OUT of window
    "3,Red Wine,16,Stowaway Bar,2026-07-17,,$421.78,$464.00\n"
    # Marilyna's (dine-in pizza) in window
    "4,Dine-in Pizza,19,Stowaway Bar,2026-07-17,,$45.45,$50.00\n"
    # Marilyna's (soft drinks) out of window
    "5,Marilyna's Soft Drinks,15,Stowaway Bar,2026-07-17,,$13.64,$15.00\n"
    # Delivery Cocktails -> STOW, in window (per 2026-07-16 canon)
    "6,Delivery Cocktails,19,Stowaway Bar,2026-07-17,,$50.00,$55.00\n"
    # HG food on Stow till -> dropped from both
    "7,Harry Gatos Food,19,Stowaway Bar,2026-07-17,,$100.00,$110.00\n"
)


def test_ingest_splits_and_windows(tmp_path):
    csv_p = tmp_path / "stow_hourly_2026-07-17.csv"
    out_p = tmp_path / "stow_hourly_2026-07-17.json"
    csv_p.write_text(SAMPLE)

    out = ingest_hourly.ingest(str(csv_p), str(out_p))

    sp = out["stowaway_proper"]
    ma = out["marilynas"]

    # Stow window = Cocktails 1090 + Gin 69 + Delivery Cocktails 55 = 1214.00
    assert sp["window_1700_2059_inc_gst"] == "1214.00"
    # Red Wine (hr 16) excluded from window but in day total: +464 = 1678.00
    assert sp["day_total_inc_gst"] == "1678.00"

    # Mari dinner window = Dine-in Pizza 50 (hr 19); soft drinks hr15 excluded
    assert ma["window_1700_2059_inc_gst"] == "50.00"
    assert ma["dinner_window_1700_2059_inc_gst"] == "50.00"
    assert ma["day_total_inc_gst"] == "65.00"

    # Lunch window (12-15): Mari soft drinks at hr15 = $15; Stow has none in 12-15
    # (Red Wine is hr16, outside both windows).
    assert ma["lunch_window_1200_1559_inc_gst"] == "15.00"
    assert sp["lunch_window_1200_1559_inc_gst"] == "0.00"
    assert sp["dinner_window_1700_2059_inc_gst"] == "1214.00"

    # HG food excluded from both scopes entirely
    disk = json.loads(out_p.read_text())
    assert disk["rows_ingested"] == 7
    # 110 (HG food) appears in neither day total
    assert disk["stowaway_proper"]["day_total_inc_gst"] == "1678.00"


def test_missing_column_fails_loud(tmp_path):
    bad = tmp_path / "stow_hourly_2026-07-18.csv"
    bad.write_text("Reporting Group Name,Site Name\nGin,Stowaway Bar\n")
    try:
        ingest_hourly.ingest(str(bad), str(tmp_path / "out.json"))
        assert False, "expected SystemExit on missing column"
    except SystemExit as e:
        assert "no column matching" in str(e)
