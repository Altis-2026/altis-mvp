"""
test_ingestion.py — Pure-function tests for backend/ingestion.py.

No FastAPI, no network, no DB. Covers file parsing for every supported
format, fuzzy column mapping, address standardization, and preview assembly.
"""
import io

import pandas as pd
import pytest

from backend.ingestion import (
    IngestionError,
    apply_mapping,
    build_preview,
    parse_upload,
    standardize_address,
    suggest_column_mapping,
)

CSV_BYTES = (
    b"Policy #,Street Address,TIV\n"
    b"P100,1600 Pennsylvania Ave NW Washington DC 20500,500000\n"
    b"P101,350 Fifth Ave New York NY 10118,750000\n"
)


# ── parse_upload ──────────────────────────────────────────────────────────────

def test_parse_upload_csv():
    df = parse_upload("portfolio.csv", CSV_BYTES)
    assert list(df.columns) == ["Policy #", "Street Address", "TIV"]
    assert len(df) == 2
    assert df.iloc[0]["Policy #"] == "P100"


def test_parse_upload_xlsx_round_trip():
    df_in = pd.DataFrame({
        "Policy #": ["P100", "P101"],
        "Street Address": ["123 Main St", "456 Oak Ave"],
        "TIV": ["500000", "750000"],
    })
    buf = io.BytesIO()
    df_in.to_excel(buf, index=False, engine="openpyxl")

    df = parse_upload("portfolio.xlsx", buf.getvalue())
    assert list(df.columns) == ["Policy #", "Street Address", "TIV"]
    assert len(df) == 2


def test_parse_upload_xls_round_trip():
    pytest.importorskip("xlwt")
    df_in = pd.DataFrame({"Policy #": ["P100"], "Street Address": ["123 Main St"]})
    buf = io.BytesIO()
    df_in.to_excel(buf, index=False, engine="xlwt")
    df = parse_upload("portfolio.xls", buf.getvalue())
    assert list(df.columns) == ["Policy #", "Street Address"]


def test_parse_upload_pdf_with_table():
    reportlab = pytest.importorskip("reportlab")
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib import colors

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf)
    data = [
        ["Policy #", "Street Address", "TIV"],
        ["P100", "123 Main St Springfield IL 62701", "500000"],
        ["P101", "456 Oak Ave Chicago IL 60601", "750000"],
    ]
    table = Table(data)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    doc.build([table])

    df = parse_upload("portfolio.pdf", buf.getvalue())
    assert list(df.columns) == ["Policy #", "Street Address", "TIV"]
    assert len(df) == 2


def test_parse_upload_pdf_without_table_raises():
    reportlab = pytest.importorskip("reportlab")
    from reportlab.platypus import SimpleDocTemplate, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf)
    styles = getSampleStyleSheet()
    doc.build([Paragraph("Just some unstructured text, no table here.", styles["Normal"])])

    with pytest.raises(IngestionError, match="No table could be detected"):
        parse_upload("portfolio.pdf", buf.getvalue())


def test_parse_upload_unsupported_extension_raises():
    with pytest.raises(IngestionError, match="Unsupported file type"):
        parse_upload("portfolio.txt", b"whatever")


def test_parse_upload_empty_csv_raises():
    with pytest.raises(IngestionError):
        parse_upload("empty.csv", b"")


def test_parse_upload_malformed_xlsx_raises():
    with pytest.raises(IngestionError, match="Could not parse XLSX"):
        parse_upload("broken.xlsx", b"not a real xlsx file")


# ── suggest_column_mapping ────────────────────────────────────────────────────

def test_suggest_column_mapping_clean_headers():
    mapping = suggest_column_mapping(["Policy #", "Street Address", "TIV", "City", "State", "Zip"])
    assert mapping["policy_number"]["matched_column"] == "Policy #"
    assert mapping["address"]["matched_column"] == "Street Address"
    assert mapping["coverage_amount"]["matched_column"] == "TIV"
    assert mapping["city"]["matched_column"] == "City"
    assert mapping["state"]["matched_column"] == "State"
    assert mapping["zip"]["matched_column"] == "Zip"
    for field in mapping.values():
        assert field["confidence"] > 0.5


def test_suggest_column_mapping_no_double_claiming_a_column():
    mapping = suggest_column_mapping(["Address"])
    claimed = [v["matched_column"] for v in mapping.values() if v["matched_column"]]
    assert claimed.count("Address") == 1


def test_suggest_column_mapping_unmatched_field_is_none_not_guessed():
    mapping = suggest_column_mapping(["xyz123", "foobar", "qqq"])
    for field, result in mapping.items():
        assert result["matched_column"] is None
        assert result["confidence"] == 0.0


def test_suggest_column_mapping_handles_abbreviations():
    mapping = suggest_column_mapping(["Pol No", "Site Address", "Sum Insured"])
    assert mapping["policy_number"]["matched_column"] == "Pol No"
    assert mapping["address"]["matched_column"] == "Site Address"
    assert mapping["coverage_amount"]["matched_column"] == "Sum Insured"


# ── standardize_address ───────────────────────────────────────────────────────

def test_standardize_address_full_clean_address():
    result = standardize_address("123 Main St, Springfield, IL 62701")
    assert result["street"] == "123 Main St"
    assert result["city"] == "Springfield"
    assert result["state"] == "IL"
    assert result["zip"] == "62701"
    assert result["parse_confidence"] == 1.0


def test_standardize_address_with_unit_number():
    result = standardize_address("456 Oak Ave Apt 3B, Chicago, IL 60601")
    assert "3B" in result["street"]
    assert result["city"] == "Chicago"
    assert result["parse_confidence"] == 1.0


def test_standardize_address_garbage_never_raises():
    result = standardize_address("!!!totally not an address###")
    assert result["normalized"] == "!!!totally not an address###"
    assert result["parse_confidence"] < 0.5


def test_standardize_address_empty_string():
    result = standardize_address("")
    assert result == {
        "normalized": "", "street": "", "city": "", "state": "",
        "zip": "", "parse_confidence": 0.0,
    }


def test_standardize_address_bare_street_low_confidence():
    result = standardize_address("123 Main St")
    assert result["parse_confidence"] < 1.0


# ── apply_mapping / build_preview ─────────────────────────────────────────────

def test_apply_mapping_single_address_column():
    df = parse_upload("portfolio.csv", CSV_BYTES)
    mapping = {"policy_number": "Policy #", "address": "Street Address", "coverage_amount": "TIV"}
    out = apply_mapping(df, mapping)
    assert list(out["address"]) == [
        "1600 Pennsylvania Ave NW Washington DC 20500",
        "350 Fifth Ave New York NY 10118",
    ]
    assert list(out["policy_number"]) == ["P100", "P101"]


def test_apply_mapping_assembles_address_from_parts_when_no_single_column():
    df = pd.DataFrame({
        "Town": ["Springfield", "Chicago"],
        "Region": ["IL", "IL"],
    })
    mapping = {"address": None, "city": "Town", "state": "Region"}
    out = apply_mapping(df, mapping)
    assert out["address"].tolist() == ["Springfield, IL", "Chicago, IL"]


def test_apply_mapping_missing_optional_fields_become_empty():
    df = pd.DataFrame({"Addr": ["123 Main St"]})
    mapping = {"address": "Addr"}
    out = apply_mapping(df, mapping)
    assert out["policy_number"].tolist() == [""]
    assert out["coverage_amount"].tolist() == [""]


def test_build_preview_end_to_end():
    df = parse_upload("portfolio.csv", CSV_BYTES)
    mapping = {"policy_number": "Policy #", "address": "Street Address", "coverage_amount": "TIV"}
    preview = build_preview(df, mapping)
    assert preview["row_count"] == 2
    assert len(preview["preview_rows"]) == 2
    assert preview["flagged_count"] == 0
    assert preview["preview_rows"][0]["standardized_address"]


def test_build_preview_flags_low_confidence_addresses():
    df = pd.DataFrame({"Addr": ["123 Main St, Springfield, IL 62701", "asdkjasdkj garbage"]})
    mapping = {"address": "Addr"}
    preview = build_preview(df, mapping)
    assert preview["flagged_count"] == 1
    assert preview["flagged_rows"][0]["address"] == "asdkjasdkj garbage"


def test_build_preview_respects_max_rows():
    df = pd.DataFrame({"Addr": [f"{i} Main St, Springfield, IL 62701" for i in range(30)]})
    mapping = {"address": "Addr"}
    preview = build_preview(df, mapping, max_rows=5)
    assert preview["row_count"] == 30
    assert len(preview["preview_rows"]) == 5
