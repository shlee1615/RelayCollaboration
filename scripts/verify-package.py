"""Verify a release without extracting files or running packaged code."""
import argparse
from pathlib import Path
import json
import sys
from package import PackageError, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    sidecar = args.manifest or args.archive.with_suffix(".manifest.json")
    try:
        print(json.dumps(verify(args.archive, sidecar), indent=2))
        return 0
    except (OSError, PackageError) as exc:
        print("Verification failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
