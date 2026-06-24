"""
ingestion.py — Universal portfolio file parsing + fuzzy column mapping.

Supports .csv / .xlsx / .xls / .pdf. Every function here is pure (no FastAPI,
no network, no DB) so it can be unit-tested directly. The PDF path never
silently guesses at row/column structure — if no table can be extracted, it
raises IngestionError so the caller can ask the user to re-export as CSV/XLSX
rather than producing a wrong portfolio.
"""
import io
from typing import Optional

import pandas as pd
from rapidfuzz import fuzz

try:
    import usaddress
except ImportError:  # pragma: no cover
    usaddress = None


class IngestionError(Exception):
    """Raised when a file can't be parsed into a row/column table."""


# ── File parsing ──────────────────────────────────────────────────────────────

def parse_upload(filename: str, content: bytes) -> pd.DataFrame:
    """
    Dispatch on file extension and return a DataFrame of raw rows.
    Every column is read as a plain string (dtype=str) so downstream mapping
    and coverage-amount parsing happen explicitly, not via pandas' type
    inference silently mangling policy numbers like "007".
    """
    name = filename.lower()

    if name.endswith('.csv'):
        try:
            df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
        except Exception as e:
            raise IngestionError(f"Could not parse CSV: {e}")

    elif name.endswith('.xlsx'):
        try:
            df = pd.read_excel(io.BytesIO(content), engine='openpyxl', dtype=str)
        except Exception as e:
            raise IngestionError(f"Could not parse XLSX: {e}")

    elif name.endswith('.xls'):
        try:
            df = pd.read_excel(io.BytesIO(content), engine='xlrd', dtype=str)
        except Exception as e:
            raise IngestionError(f"Could not parse XLS: {e}")

    elif name.endswith('.pdf'):
        df = _parse_pdf_table(content)

    else:
        raise IngestionError(
            f"Unsupported file type for '{filename}'. "
            "Upload a .csv, .xlsx, .xls, or .pdf file."
        )

    df = df.fillna('')
    df.columns = [str(c).strip() for c in df.columns]

    if df.empty or len(df.columns) == 0:
        raise IngestionError("File parsed but contains no rows.")

    return df


def _parse_pdf_table(content: bytes) -> pd.DataFrame:
    """
    Extract the largest table found across all pages. Raises IngestionError
    if no table is detected — never falls back to guessing column splits
    from raw text.
    """
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover
        raise IngestionError("PDF support is not installed (pdfplumber missing).")

    best_table = None
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    if not table or len(table) < 2:
                        continue
                    if best_table is None or len(table) > len(best_table):
                        best_table = table
    except Exception as e:
        raise IngestionError(f"Could not read PDF: {e}")

    if best_table is None:
        raise IngestionError(
            "No table could be detected in this PDF. Export the portfolio "
            "as CSV or XLSX instead — Altis won't guess at unstructured "
            "PDF layouts."
        )

    header, *rows = best_table
    header = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(header)]
    return pd.DataFrame(rows, columns=header)


# ── Fuzzy column mapping ──────────────────────────────────────────────────────

CANONICAL_FIELD_ALIASES = {
    'policy_number':   ['policy number', 'policy #', 'policy no', 'policy_number',
                         'pol number', 'policy id', 'policy'],
    'address':         ['address', 'street address', 'property address',
                         'site address', 'location', 'full address'],
    'coverage_amount': ['coverage amount', 'coverage amt', 'tiv',
                         'total insured value', 'coverage', 'sum insured',
                         'limit', 'insured value'],
    'city':            ['city', 'town'],
    'state':           ['state', 'st', 'province'],
    'zip':             ['zip', 'zip code', 'postal code', 'zipcode', 'postcode'],
}

REQUIRED_FIELDS = ('address',)
MATCH_THRESHOLD = 60.0


def suggest_column_mapping(columns: list[str]) -> dict[str, dict]:
    """
    For each canonical field, find the uploaded column whose header best
    matches one of its known aliases. Each uploaded column can only be
    claimed by one field. Returns matched_column=None (never a low-quality
    guess) when nothing clears MATCH_THRESHOLD.
    """
    used = set()
    result = {}

    for field, aliases in CANONICAL_FIELD_ALIASES.items():
        best_col, best_score = None, 0.0
        for col in columns:
            if col in used:
                continue
            score = max(fuzz.WRatio(col.lower().strip(), alias) for alias in aliases)
            if score > best_score:
                best_score, best_col = score, col

        if best_score >= MATCH_THRESHOLD:
            result[field] = {'matched_column': best_col, 'confidence': round(best_score / 100, 2)}
            used.add(best_col)
        else:
            result[field] = {'matched_column': None, 'confidence': 0.0}

    return result


# ── Address standardization ──────────────────────────────────────────────────

_TAG_TO_PART = {
    'AddressNumber':         'house_number',
    'StreetNamePreType':     'street',
    'StreetName':            'street',
    'StreetNamePostType':    'street',
    'StreetNamePostDirectional': 'street',
    'OccupancyType':         'unit',
    'OccupancyIdentifier':   'unit',
    'PlaceName':             'city',
    'StateName':             'state',
    'ZipCode':               'zip',
}


def standardize_address(raw: str) -> dict:
    """
    Parse a free-text address into components via usaddress. Never raises —
    on anything usaddress can't confidently tag, falls back to the original
    string with parse_confidence=0.0 so the caller can flag it for review
    rather than silently mis-geocoding it.
    """
    raw = (raw or '').strip()
    if not raw:
        return {'normalized': '', 'street': '', 'city': '', 'state': '',
                'zip': '', 'parse_confidence': 0.0}

    if usaddress is None:  # pragma: no cover
        return {'normalized': raw, 'street': raw, 'city': '', 'state': '',
                'zip': '', 'parse_confidence': 0.0}

    try:
        tagged, label = usaddress.tag(raw)
    except usaddress.RepeatedLabelError:
        return {'normalized': raw, 'street': raw, 'city': '', 'state': '',
                'zip': '', 'parse_confidence': 0.2}
    except Exception:
        return {'normalized': raw, 'street': raw, 'city': '', 'state': '',
                'zip': '', 'parse_confidence': 0.0}

    if label != 'Street Address':
        return {'normalized': raw, 'street': raw, 'city': '', 'state': '',
                'zip': '', 'parse_confidence': 0.2}

    parts = {'house_number': [], 'street': [], 'unit': [], 'city': '',
             'state': '', 'zip': ''}
    for tag, token in tagged.items():
        part = _TAG_TO_PART.get(tag)
        if part in ('house_number', 'street', 'unit'):
            parts[part].append(token)
        elif part in ('city', 'state', 'zip'):
            parts[part] = token

    street = ' '.join(parts['house_number'] + parts['street'])
    unit = ' '.join(parts['unit'])
    line1 = f"{street} {unit}".strip()

    normalized_parts = [p for p in [line1, parts['city'], parts['state'], parts['zip']] if p]
    normalized = ', '.join(normalized_parts) if normalized_parts else raw

    # Confidence reflects how much of the address usaddress could place,
    # not just whether it parsed at all — a bare street with no city/state
    # is materially less trustworthy for geocoding than a full match.
    found = sum(1 for v in (street, parts['city'], parts['state'], parts['zip']) if v)
    confidence = round(found / 4, 2)

    return {
        'normalized':        normalized,
        'street':            line1,
        'city':              parts['city'],
        'state':             parts['state'],
        'zip':               parts['zip'],
        'parse_confidence':  confidence,
    }


# ── Preview assembly ──────────────────────────────────────────────────────────

def apply_mapping(df: pd.DataFrame, mapping: dict[str, Optional[str]]) -> pd.DataFrame:
    """
    Build a canonical-column DataFrame from the raw upload using the
    confirmed {field: source_column} mapping. Missing optional fields become
    empty columns; address is assembled from street+city+state+zip when no
    single address column was mapped.
    """
    out = pd.DataFrame(index=df.index)

    for field in ('policy_number', 'coverage_amount'):
        col = mapping.get(field)
        out[field] = df[col] if col and col in df.columns else ''

    addr_col = mapping.get('address')
    if addr_col and addr_col in df.columns:
        out['address'] = df[addr_col]
    else:
        parts = []
        for field in ('address', 'city', 'state', 'zip'):
            col = mapping.get(field)
            if col and col in df.columns:
                parts.append(df[col].astype(str))
        if parts:
            combined = parts[0]
            for p in parts[1:]:
                combined = combined.str.cat(p, sep=', ')
            out['address'] = combined
        else:
            out['address'] = ''

    return out


def build_preview(df: pd.DataFrame, mapping: dict[str, Optional[str]],
                   max_rows: int = 20) -> dict:
    """
    Apply the mapping, standardize addresses, and return a bounded preview
    for the frontend's review screen: sample rows, full row count, and a
    list of rows whose address didn't standardize cleanly (confidence < 0.5).
    """
    mapped = apply_mapping(df, mapping)
    standardized = mapped['address'].apply(standardize_address)

    mapped = mapped.copy()
    mapped['standardized_address'] = [s['normalized'] for s in standardized]
    mapped['address_confidence'] = [s['parse_confidence'] for s in standardized]

    flagged = mapped[mapped['address_confidence'] < 0.5]

    return {
        'row_count':     len(mapped),
        'preview_rows':  mapped.head(max_rows).to_dict('records'),
        'flagged_count': len(flagged),
        'flagged_rows':  flagged.head(max_rows).to_dict('records'),
    }
