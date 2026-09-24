"""
opendata.py — Commercial-SAR open data as an independent check on Altis.

Umbra (AWS Open Data, CC-BY 4.0) and ICEYE (Open SAR Data, AWS Registry) both
publish static STAC catalogs of sub-metre to few-metre X-band SAR. Their
coverage is opportunistic — whatever they happened to task — so they cannot
power a product. What they can do is *check* one: where a high-resolution
scene overlaps an Altis flood result in space and time, the scene's water
mask is an independent reference and the agreement is a measured number
rather than an asserted confidence.

This module finds the overlaps. The agreement arithmetic lives in
pipeline/validation_metrics.py. Search results are cached, and a search that
finds nothing is reported as exactly that: "checked N archive items, 0
overlap", which is itself a useful, honest statement.

Both catalogs encode acquisition dates in their object keys, so we list keys,
filter by date, and fetch only candidate item JSONs.
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

from backend import geodata

UMBRA_BUCKET = 'https://umbra-open-data-catalog.s3.us-west-2.amazonaws.com'
ICEYE_BUCKET = 'https://iceye-open-data-catalog.s3.amazonaws.com'
_NS = '{http://s3.amazonaws.com/doc/2006-03-01/}'


def list_keys(bucket: str, prefix: str, session=None, max_pages: int = 20) -> list[str]:
    keys, token = [], None
    for _ in range(max_pages):
        params = {'list-type': '2', 'prefix': prefix}
        if token:
            params['continuation-token'] = token
        r = geodata._get(bucket + '/', session=session, timeout=40, params=params)
        if r.status_code != 200:
            raise geodata.GeoDataError(f'S3 listing HTTP {r.status_code}')
        root = ET.fromstring(r.content)
        keys += [c.find(_NS + 'Key').text for c in root.findall(_NS + 'Contents')]
        if (root.findtext(_NS + 'IsTruncated') or 'false') != 'true':
            break
        token = root.findtext(_NS + 'NextContinuationToken')
    return keys


def _intersects(a, b):
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _item_summary(item: dict, archive: str, url: str) -> dict:
    p = item.get('properties', {})
    assets = item.get('assets', {})
    gec = next((v.get('href') for k, v in assets.items()
                if 'GEC' in k.upper() or k.lower().endswith('.tif') or 'tif' in (v.get('type') or '')), None)
    return {'archive': archive, 'id': item.get('id'), 'datetime': p.get('datetime'),
            'bbox': item.get('bbox'), 'resolution_m': p.get('sar:resolution_range') or p.get('gsd'),
            'mode': p.get('sar:instrument_mode') or p.get('iceye:acquisition_mode'),
            'item_url': url, 'geotiff': gec}


def _date_from_key(key: str):
    m = re.search(r'(20\d{2})-(\d{2})-(\d{2})', key) or re.search(r'_(20\d{2})(\d{2})(\d{2})T', key)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def search(bbox, start: date, end: date, session=None) -> dict:
    """Overlapping Umbra + ICEYE items in [start, end] for bbox=[w,s,e,n]."""
    key = hashlib.md5(f'od{bbox}{start}{end}'.encode()).hexdigest()[:16]
    path = geodata._cache_path('opendata', key, 'json')
    if path.exists():
        return json.loads(path.read_text())
    report = {'bbox': bbox, 'window': [start.isoformat(), end.isoformat()], 'archives': {}, 'overlaps': []}
    # Umbra: stac/YYYY/YYYY-MM/YYYY-MM-DD/<id>/<id>.json (STAC since 2023).
    try:
        months = sorted({(d.year, d.month) for d in
                         (start + timedelta(days=k) for k in range((end - start).days + 1))})
        cand = []
        for y, m in months:
            cand += [k for k in list_keys(UMBRA_BUCKET, f'stac/{y}/{y}-{m:02d}/', session)
                     if k.endswith('.json') and not k.endswith('catalog.json')]
        cand = [k for k in cand if (d := _date_from_key(k)) and start <= d <= end]
        hits = []
        for k in cand[:400]:
            r = geodata._get(f'{UMBRA_BUCKET}/{k}', session=session, timeout=30)
            if r.status_code == 200:
                it = r.json()
                if it.get('bbox') and _intersects(it['bbox'][:4] if len(it['bbox']) == 4 else
                                                  [it['bbox'][0], it['bbox'][1], it['bbox'][3], it['bbox'][4]], bbox):
                    hits.append(_item_summary(it, 'Umbra', f'{UMBRA_BUCKET}/{k}'))
        report['archives']['umbra'] = {'ok': True, 'items_in_window': len(cand), 'overlapping': len(hits),
                                       'note': 'Umbra STAC catalog covers 2023 onward'}
        report['overlaps'] += hits
    except Exception as ex:  # noqa: BLE001
        report['archives']['umbra'] = {'ok': False, 'reason': f'{type(ex).__name__}: {ex}'[:200]}
    # ICEYE: stac-items/YYYY/MM/ICEYE_<id>_<YYYYMMDDTHHMMSSZ>_….json
    try:
        keys = [k for k in list_keys(ICEYE_BUCKET, 'stac-items/', session) if k.endswith('.json')]
        cand = [k for k in keys if (d := _date_from_key(k)) and start <= d <= end]
        hits = []
        for k in cand[:400]:
            r = geodata._get(f'{ICEYE_BUCKET}/{k}', session=session, timeout=30)
            if r.status_code == 200:
                it = r.json()
                bb = it.get('bbox')
                if bb and _intersects(bb[:4] if len(bb) == 4 else [bb[0], bb[1], bb[3], bb[4]], bbox):
                    hits.append(_item_summary(it, 'ICEYE', f'{ICEYE_BUCKET}/{k}'))
        report['archives']['iceye'] = {'ok': True, 'items_total': len(keys), 'items_in_window': len(cand),
                                       'overlapping': len(hits)}
        report['overlaps'] += hits
    except Exception as ex:  # noqa: BLE001
        report['archives']['iceye'] = {'ok': False, 'reason': f'{type(ex).__name__}: {ex}'[:200]}
    report['searched_at'] = datetime.utcnow().isoformat() + 'Z'
    if all(a.get('ok') for a in report['archives'].values()):
        path.write_text(json.dumps(report))
    return report
