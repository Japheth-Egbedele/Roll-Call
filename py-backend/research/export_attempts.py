"""Export verification_attempts from MongoDB and print FAR/FRR metrics."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from verification_metrics import export_mongo_attempts

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mongo-uri", default=os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    parser.add_argument("--out", type=Path, default=Path("research/output/mongo_attempts.csv"))
    args = parser.parse_args()

    attempts, metrics = export_mongo_attempts(args.mongo_uri, out_csv=args.out)
    print(json.dumps(metrics, indent=2))
    print(f"Exported {len(attempts)} rows to {args.out}")


if __name__ == "__main__":
    main()
