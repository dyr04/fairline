"""T1 verify: tree exists, pytest+ruff exit 0, contracts documented."""
import subprocess
import sys
from pathlib import Path

need = ["src/poller.py", "src/devig.py", "src/signal_engine.py", "src/backtest.py",
        "src/sharpness.py", "src/alerts.py", "src/db.py", "src/staking.py",
        "dashboard/app.py", "config.yaml", ".env.example", "DECISIONS.md",
        ".github/workflows/poll.yml", "tests", "scripts"]
missing = [p for p in need if not Path(p).exists()]
if missing:
    sys.exit(f"FAIL missing: {missing}")
for cmd in (["python3", "-m", "pytest", "-q"], ["python3", "-m", "ruff", "check", "."]):
    if subprocess.run(cmd).returncode != 0:
        sys.exit("FAIL: " + " ".join(cmd))
dec = Path("DECISIONS.md").read_text()
assert "%Y-%m-%dT%H:%M:%SZ" in dec and "manifest.json" in dec, "contracts missing"
print("verify_t1: OK")
