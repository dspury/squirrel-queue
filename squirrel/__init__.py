"""Squirrel v1 — Execution substrate for the harness system."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent / ".squirrel"
INBOX = BASE_DIR / "inbox"
REGISTRY = BASE_DIR / "registry"
LANES = BASE_DIR / "lanes"
OUTBOX = BASE_DIR / "outbox"
CONTROL = BASE_DIR / "control"
SCHEMAS = BASE_DIR / "schemas"
CONFIG = BASE_DIR / "config"
RUNTIME = BASE_DIR / "runtime"
RUNTIME_LANES = RUNTIME / "lanes"

_REQUIRED_DIRS = [
    INBOX, REGISTRY, LANES, OUTBOX, CONTROL, SCHEMAS, CONFIG,
    RUNTIME, RUNTIME_LANES,
]


def ensure_workspace():
    """Create any missing workspace directories.

    Called at the start of every runner cycle. If .squirrel/ is damaged
    or partially deleted, this recovers the structure silently.
    Schemas and config files are NOT recreated — those are project
    artifacts, not runtime state.
    """
    for d in _REQUIRED_DIRS:
        d.mkdir(parents=True, exist_ok=True)
