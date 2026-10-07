"""Compatibility entry for source installs; executable builds use Relay.exe install."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from relay_collaboration.skill_install import main

if __name__ == '__main__':
    raise SystemExit(main())
