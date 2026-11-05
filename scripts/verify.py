"""Capture actual verification output; nonzero exit status is preserved."""
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    (ROOT / "tmp").mkdir(exist_ok=True)
    (ROOT / "docs" / "evidence").mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "pytest", "-v", "--no-header", "--tb=short",
               "--basetemp=" + str(ROOT / "tmp" / ("tests-" + uuid4().hex[:8]))]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    output = result.stdout + result.stderr
    (ROOT / "docs" / "evidence" / "test-results.txt").write_text(output, encoding="utf-8")
    print(output)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
