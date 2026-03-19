"""Atomic JSON persistence — write to temp file then rename to prevent corruption."""

import json
import os
import tempfile


def load_json(path: str, default=None):
    """Load JSON file, returning default if missing or corrupt."""
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default if default is not None else {}


def save_json(path: str, data, indent: int = 2):
    """Atomically save JSON — writes to temp file then renames."""
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent, default=str)
        os.replace(tmp_path, path)  # atomic on POSIX
    except:
        os.unlink(tmp_path)  # clean up temp file on error
        raise
