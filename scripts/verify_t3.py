import subprocess
import sys

r = subprocess.run(["python3", "-m", "pytest", "tests/test_devig.py", "-q"])
sys.exit(r.returncode if r.returncode else print("verify_t3: OK") or 0)
