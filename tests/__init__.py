"""Isolated fixture suites; all writable scratch belongs in work/."""
from pathlib import Path

WORK = Path(__file__).resolve().parents[1] / "work"
WORK.mkdir(exist_ok=True)
