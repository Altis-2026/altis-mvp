# provenance.py — Run manifest for pipeline outputs (audit trail foundation)
import json
import os
from datetime import datetime, timezone

from config import OUTPUT_DIR, PIPELINE_VERSION


def _manifest_path(event_id):
    return os.path.join(OUTPUT_DIR, f"{event_id}_manifest.json")


def write_manifest(event_id, stage, data):
    """
    Merge `data` under `stage` into outputs/{event_id}_manifest.json.
    Each pipeline stage (properties, flood_detection, triage) records its own
    inputs so a final CSV's lineage — scene dates, DEM used, thresholds applied —
    can be reconstructed without re-running anything.
    """
    path = _manifest_path(event_id)
    manifest = {}
    if os.path.exists(path):
        with open(path) as f:
            manifest = json.load(f)

    manifest['event_id'] = event_id
    manifest['pipeline_version'] = PIPELINE_VERSION
    manifest.setdefault('stages', {})
    manifest['stages'][stage] = {
        **data,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(manifest, f, indent=2)

    return path
