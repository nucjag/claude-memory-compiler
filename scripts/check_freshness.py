"""Check whether every current daily log matches the compiled manifest.

Exit codes: 0 = fresh, 1 = stale, 2 = technical failure.
"""

from __future__ import annotations

import sys

from utils import file_hash, list_raw_files, load_compiled_daily


def main() -> int:
    try:
        compiled = load_compiled_daily()
        current = {path.name: file_hash(path) for path in list_raw_files()}
    except Exception as exc:
        print(f"Freshness check technical failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    stale = sorted(
        name for name in current
        if current.get(name) != compiled.get(name)
    )
    if stale:
        print("Daily files need compilation:")
        for name in stale:
            print(f"  - daily/{name}")
        return 1

    print(f"All {len(current)} daily file(s) match the compiled manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
