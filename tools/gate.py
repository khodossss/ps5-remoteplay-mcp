"""Quality gate — one call: `uv run python tools/gate.py`. Lint, format check, tests (hardware ones skip
unless PS5_HOST is set)."""

import subprocess
import sys

STEPS = [
    ["ruff", "check", "src", "tests", "tools/gate.py"],
    ["ruff", "format", "--check", "src", "tests", "tools/gate.py"],
    ["pytest", "-q"],
]

for step in STEPS:
    print("$", " ".join(step), flush=True)
    if subprocess.call([sys.executable, "-m", *step]) != 0:
        sys.exit(f"gate failed at: {' '.join(step)}")
print("gate passed")
