"""Download and derive a reproducible OASST1 self-evolution dataset."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

from mewcode.evolution.real_dataset import (
    OASST1_DATASET,
    OASST1_REVISION,
    build_source_manifest,
    derive_oasst1_cases,
    write_jsonl,
)


DATASET_SERVER_ROWS_URL = "https://datasets-server.huggingface.co/rows"
DEFAULT_RAW_DIR = ".mewcode/evolution/datasets/oasst1"
DEFAULT_DERIVED_PATH = "benchmarks/oasst1_derived_cases.jsonl"
DEFAULT_MANIFEST_PATH = "benchmarks/oasst1_derived_manifest.json"
PAGE_SIZE = 100


def download_oasst1_rows(
    output_path: str | Path,
    *,
    split: str = "validation",
    revision: str = OASST1_REVISION,
    row_limit: int = 1000,
    timeout_seconds: float = 30.0,
) -> int:
    """Download bounded raw rows from a pinned dataset-server revision."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with output.open("w", encoding="utf-8") as handle:
        for offset in range(0, max(0, row_limit), PAGE_SIZE):
            length = min(PAGE_SIZE, row_limit - offset)
            params = urllib.parse.urlencode({
                "dataset": OASST1_DATASET,
                "config": "default",
                "split": split,
                "offset": offset,
                "length": length,
                "revision": revision,
            })
            request = urllib.request.Request(
                f"{DATASET_SERVER_ROWS_URL}?{params}",
                headers={"User-Agent": "mewcode-self-evolution-eval/1"},
            )
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = json.load(response)
            page = payload.get("rows", []) if isinstance(payload, dict) else []
            if not isinstance(page, list) or not page:
                break
            for item in page:
                row = item.get("row") if isinstance(item, dict) else None
                if not isinstance(row, dict):
                    continue
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                total += 1
            if len(page) < length:
                break
    return total


def load_raw_rows(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download pinned OASST1 rows and write sanitized derived cases.",
    )
    parser.add_argument("--split", default="validation")
    parser.add_argument("--revision", default=OASST1_REVISION)
    parser.add_argument("--row-limit", type=int, default=1000)
    parser.add_argument("--raw-dir", default=DEFAULT_RAW_DIR)
    parser.add_argument("--derived", default=DEFAULT_DERIVED_PATH)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout per dataset-server page.",
    )
    args = parser.parse_args()

    raw_path = (
        Path(args.raw_dir)
        / f"{args.split}-{args.revision[:12]}-{args.row_limit}.jsonl"
    )
    row_count = download_oasst1_rows(
        raw_path,
        split=args.split,
        revision=args.revision,
        row_limit=args.row_limit,
        timeout_seconds=args.timeout,
    )
    rows = load_raw_rows(raw_path)
    cases = derive_oasst1_cases(rows, revision=args.revision)
    derived_count = write_jsonl(args.derived, cases)
    source_manifest = build_source_manifest(
        raw_path,
        dataset=OASST1_DATASET,
        split=args.split,
        revision=args.revision,
        row_count=row_count,
    )
    manifest = {
        **source_manifest,
        "raw_retention": "local-cache-only",
        "derived_path": str(args.derived),
        "derived_case_count": derived_count,
        "privacy": {
            "raw_text_in_derived": False,
            "user_ids_in_derived": False,
            "message_ids_in_derived": False,
        },
    }
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
