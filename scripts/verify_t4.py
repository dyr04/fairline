import subprocess
import sys

r = subprocess.run(["python3", "-m", "pytest", "tests/test_signal_engine.py", "-q"])
sys.exit(r.returncode if r.returncode else print("verify_t4: OK") or 0)
