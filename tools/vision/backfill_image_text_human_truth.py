"""Backfill existing human-confirmed truth into the image-text dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from betguard.vision.image_text_acceptance import default_dataset_root
from betguard.vision.image_text_truth_backfill import backfill_existing_human_truth


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, default=default_dataset_root())
    args = parser.parse_args()

    report = backfill_existing_human_truth(
        args.source_root,
        acceptance_dataset_root=args.dataset_root,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
