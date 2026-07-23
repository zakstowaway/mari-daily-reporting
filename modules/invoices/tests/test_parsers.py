"""
Parser-layer unit tests.

The parsers themselves read real PDFs (tested against the corpus by
parser_regression.py). Here we lock the coordinate PRIMITIVE they all rely on —
bucketing a visual row's words into columns by x-position — since that's the
pure, PDF-free part and the thing most likely to silently drift.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.invoices import pdf_text  # noqa: E402

# Select Fresh column starts.
COLS = [("code", 0), ("desc", 78), ("order", 290), ("supply", 348),
        ("unit", 378), ("price", 460), ("total", 525)]


def _row(*words):
    # words: (x0, text) -> (x0, x1, text)
    return [(x, x + 20, t) for x, t in words]


def test_bucket_assigns_words_to_columns_by_x():
    row = _row((32, "CUCLK"), (80, "CUCUMBER"), (129, "LEBANESE"), (173, "KG"),
               (308, "2.00"), (357, "2.00"), (381, "KG"), (488, "4.10"), (547, "8.20"))
    c = pdf_text.bucket(row, COLS)
    assert c["code"] == "CUCLK"
    assert c["desc"] == "CUCUMBER LEBANESE KG"    # wrapped size stays in description
    assert c["supply"] == "2.00"
    assert c["unit"] == "KG"
    assert c["price"] == "4.10"
    assert c["total"] == "8.20"


def test_bucket_left_of_first_boundary_falls_in_first_column():
    c = pdf_text.bucket(_row((5, "X")), COLS)
    assert c["code"] == "X"


def test_word_rows_and_bucket_ignore_empty_columns():
    # a money row with no description (wrapped away) still yields the numbers
    row = _row((38, "4"), (68, "MKB500"), (381, "6.05"), (450, "0.00"), (516, "24.20"))
    c = pdf_text.bucket(row, [("qty", 0), ("sku", 64), ("unit", 143), ("desc", 198),
                              ("price", 360), ("gst", 449), ("amt", 506)])
    assert c["qty"] == "4" and c["sku"] == "MKB500"
    assert c["desc"] == "" and c["unit"] == ""
    assert c["price"] == "6.05" and c["amt"] == "24.20"


# Paramount Liquor column starts (Code | Description | Size | Case/Bottle |
# Base Cost | Total Net | WET | GST | LUC Ex GST | Total Inc GST).
PARAMOUNT_COLS = [("code", 0), ("desc", 75), ("size", 290), ("qty", 356),
                  ("base", 415), ("net", 485), ("wet", 548), ("gst", 600),
                  ("luc", 645), ("incgst", 705)]


def test_paramount_bucket_reads_total_inc_gst_on_a_wet_line():
    # CARPANO row: bottle-break qty "0 / 1", per-case base cost, WET present.
    # The reconcile figure is the rightmost Total-Inc-GST cell ($23.17).
    row = _row((21, "10015926"), (81, "CARPANO"), (128, "CLASSICO"),
               (176, "VERMOUTH"), (235, "750ml"), (296, "6/750"), (321, "ml"),
               (372, "0"), (379, "/"), (384, "1"), (428, "$98.00"),
               (494, "$16.33"), (555, "$4.74"), (609, "$2.11"), (650, "$21.07"),
               (727, "$23.17"))
    c = pdf_text.bucket(row, PARAMOUNT_COLS)
    assert c["code"] == "10015926"
    assert c["desc"].startswith("CARPANO")
    assert c["base"] == "$98.00"
    assert c["net"] == "$16.33"
    assert c["incgst"] == "$23.17"     # this is what the parser reconciles on


def test_paramount_bucket_reads_a_misc_charge_line():
    # Carton Freight (MISC) — captured as an EXTRA line; incgst = $7.15.
    row = _row((21, "9000000"), (81, "Carton"), (111, "Freight"), (296, "MISC"),
               (378, "5"), (431, "$1.30"), (496, "$6.50"), (609, "$0.65"),
               (650, "$1.30"), (730, "$7.15"))
    c = pdf_text.bucket(row, PARAMOUNT_COLS)
    assert c["code"] == "9000000"
    assert c["size"] == "MISC"
    assert c["desc"] == "Carton Freight"
    assert c["incgst"] == "$7.15"
