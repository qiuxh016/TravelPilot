"""Validate completed blind-review scores and report paired summaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tests.evaluation.blind_review import summarize_blind_review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_sheet", type=Path, help="Completed reviewer sheet")
    parser.add_argument("private_key", type=Path, help="Private arm mapping")
    parser.add_argument("output", type=Path, help="Summary JSON output")
    args = parser.parse_args()
    if args.output.resolve() in {args.review_sheet.resolve(), args.private_key.resolve()}:
        parser.error("summary output must not overwrite the input sheet or private key")
    sheet = json.loads(args.review_sheet.read_text(encoding="utf-8"))
    key = json.loads(args.private_key.read_text(encoding="utf-8"))
    summary = summarize_blind_review(sheet, key)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"paired_case_turns": summary["paired_case_turns"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
