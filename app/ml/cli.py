"""Offline U22 ML experiment entry point; does not call any football API.

python -m app.ml.cli export --cutoff 2025-06-30 --output data/ml/snapshots.jsonl
python -m app.ml.cli train --input data/ml/verified.jsonl --output data/ml/model.json
"""
import argparse
import json
from datetime import date
from pathlib import Path

from app.db import SessionLocal
from app.ml.experiment import DatasetError, train_chronological_experiment
from app.ml.features import export_unlabeled


def _write_new(path: Path, content: str) -> None:
    if not path.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {path.parent}")
    # Exclusive creation prevents accidentally overwriting audited datasets.
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline, non-production U22 ML research")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="Unlabeled, unverified feature snapshots")
    export.add_argument("--cutoff", type=date.fromisoformat, required=True)
    export.add_argument("--competition-id", type=int)
    export.add_argument(
        "--position", choices=("attacker", "midfielder", "defender", "goalkeeper"),
    )
    export.add_argument("--output", type=Path, required=True)

    train = sub.add_parser("train", help="Validated labeled JSONL to experiment JSON")
    train.add_argument("--input", type=Path, required=True)
    train.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.command == "export":
            if args.cutoff > date.today():
                raise ValueError("Cannot export future snapshots")
            with SessionLocal() as session:
                records = export_unlabeled(
                    session, cutoff=args.cutoff, competition_id=args.competition_id,
                    position=args.position,
                )
            _write_new(args.output, "".join(
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                for row in records
            ))
            print(f"Wrote {len(records)} unlabeled, NOT point-in-time-verified rows")
            print("Retrospective backfills may leak later knowledge; audit before labeling.")
        else:
            rows = []
            with args.input.open(encoding="utf-8") as handle:
                for index, line in enumerate(handle, start=1):
                    if line.strip():
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError as exc:
                            raise ValueError(f"Malformed JSON at line {index}") from exc
            artifact = train_chronological_experiment(rows)
            _write_new(args.output, json.dumps(
                artifact, sort_keys=True, indent=2,
            ) + "\n")
            print(json.dumps({
                "model": artifact["model_type"],
                "approved_for_production": artifact["approved_for_production"],
                "train_count": artifact["train_count"],
                "validation_count": artifact["validation_count"],
                "metrics": artifact["metrics"],
            }, indent=2))
    except (ValueError, DatasetError, FileExistsError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
