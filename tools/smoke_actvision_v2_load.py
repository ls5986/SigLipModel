#!/usr/bin/env python3
"""Load the exact ActVision v2 candidate locally without running inference."""

from __future__ import annotations

import argparse
from dataclasses import replace
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
        provenance = json.loads(args.vision_provenance.read_text())
        revision = provenance.get("revision")
        if not revision:
            raise ValueError(
                "The original SigLIP2 revision is absent from the supplied provenance"
            )
        expected = replace(DEFAULT_EXPECTATIONS, vision_revision=revision)
        paths = SmokePaths(
            manifest=args.bundle_root / "release.json",
            artifact_root=args.bundle_root,
            vision_provenance=args.vision_provenance,
            text_checkpoint=args.text_checkpoint,
        )
        verified = verify_inputs(paths, expected)
        report = load_only(verified, expected)
    except (OSError, ValueError) as exc:
        print(f"ActVision v2 load smoke blocked: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
