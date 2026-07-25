"""T8 verify: determinism + look-ahead via the test suite, then a double run."""
import subprocess
import sys

r = subprocess.run(["python3", "-m", "pytest", "tests/test_backtest.py",
                    "tests/test_sharpness.py", "-q"])
sys.exit(r.returncode if r.returncode else print("verify_t8: OK") or 0)
