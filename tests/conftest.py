import importlib.util
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent / "pipeline"

if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))


def load_pipeline_module(filename):
    """Load a pipeline module whose filename starts with a digit (e.g. '04_triage_notes.py')."""
    module_name = filename.replace(".py", "")
    spec = importlib.util.spec_from_file_location(module_name, PIPELINE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
