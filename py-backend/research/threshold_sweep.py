"""
FAR/FRR trade-off curve at multiple distance thresholds.

Usage:
  python research/threshold_sweep.py --dataset ../dataset
  python research/threshold_sweep.py --dataset ../dataset --taus 0.35 0.4 0.45 0.5
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_full_benchmark import run_benchmark


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[2] / "dataset")
    parser.add_argument("--taus", type=float, nargs="+", default=[0.35, 0.4, 0.45, 0.5])
    parser.add_argument("--out", type=Path, default=Path("research/output/threshold_sweep.json"))
    args = parser.parse_args()

    results = []
    for i, tau in enumerate(args.taus, start=1):
        print(f"\n[{i}/{len(args.taus)}] Running τ={tau} ... (encoding + comparisons — may take several minutes)")
        _, metrics = run_benchmark(args.dataset, tau)
        row = {
            "tau": tau,
            "FAR": metrics.get("FAR"),
            "FRR": metrics.get("FRR"),
            "accuracy": metrics.get("accuracy"),
            "TP": metrics.get("TP"),
            "TN": metrics.get("TN"),
            "FP": metrics.get("FP"),
            "FN": metrics.get("FN"),
            "n_attempts": metrics.get("n_attempts"),
            "gallery_size": metrics.get("gallery_size"),
        }
        results.append(row)
        print(f"τ={tau:.2f}  FAR={row['FAR']}  FRR={row['FRR']}  acc={row['accuracy']}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved: {args.out}")


if __name__ == "__main__":
    main()
