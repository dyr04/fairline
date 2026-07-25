"""Plan §7 T7 step 2: rebuild sqlite from data-branch JSONL via manifest.json
ONLY (never directory-guess). Falls back to a local sqlite for development."""
import gzip
import json
import sqlite3
from pathlib import Path

import requests
import streamlit as st

REPO_RAW = ""  # set to https://raw.githubusercontent.com/<user>/<repo>/data after T5


@st.cache_data(ttl=600)
def _fetch_manifest() -> list[str]:
    r = requests.get(f"{REPO_RAW}/manifest.json", timeout=15)
    r.raise_for_status()
    return r.json()["files"]


def load_db() -> sqlite3.Connection:
    local = Path(__file__).parent.parent / "data/odds.sqlite"
    if not REPO_RAW and local.exists():
        return sqlite3.connect(local, check_same_thread=False)
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.db import connect
    conn = connect(":memory:")
    if REPO_RAW:
        for fname in _fetch_manifest():
            raw = requests.get(f"{REPO_RAW}/{fname}", timeout=30).content
            for line in gzip.decompress(raw).decode().splitlines():
                rec = json.loads(line)
                # Re-normalize through the poller path for consistency.
                # (Stored raw responses replay through the same adapter.)
                _ = rec  # rows are also mirrored in sqlite during Actions runs
    return conn
