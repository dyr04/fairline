"""T5 step 3e: manifest.json is the ONLY file-discovery contract (§9 handoff fix)."""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "data-branch")
mpath = root / "manifest.json"
manifest = json.loads(mpath.read_text()) if mpath.exists() else {"files": []}
have = set(manifest["files"])
for f in sorted((root / "raw").glob("*.jsonl.gz")):
    rel = f"raw/{f.name}"
    if rel not in have:
        manifest["files"].append(rel)
mpath.write_text(json.dumps(manifest, indent=1))
print("manifest:", len(manifest["files"]), "files")
