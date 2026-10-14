#!/usr/bin/env python3
"""Load the exact ActVision v2 candidate locally without running inference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from actvision_load_smoke import (  # noqa: E402
    DEFAULT_EXPECTATIONS,
    SmokePaths,
    load_only,
    verify_inputs,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--bundle-root", type=Path, required=True)
    result.add_argument("--vision-provenance", type=Path, required=True)
    result.add_argument("--text-checkpoint", type=Path, required=True)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if DEFAULT_EXPECTATIONS.vision_revision is None:
            raise ValueError(
                "The original SigLIP2 revision has not been independently pinned"
            )
        paths = SmokePaths(
            manifest=args.bundle_root / "release.json",
            artifact_root=args.bundle_root,
            vision_provenance=args.vision_provenance,
            text_checkpoint=args.text_checkpoint,
        )
        verified = verify_inputs(paths, DEFAULT_EXPECTATIONS)
        report = load_only(verified, DEFAULT_EXPECTATIONS)
    except (OSError, ValueError) as exc:
        print(f"ActVision v2 load smoke blocked: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
