"""
Offline benchmark for paper metrics from a labeled image folder.

Folder layout:
  dataset/
    gallery/           # one enrollment image per person (reference)
      P001_enroll.jpg
      P002_enroll.jpg
    probes/
      genuine/         # probe images that SHOULD match their ID in filename prefix
        P001_probe_01.jpg
        P001_probe_02.jpg
      impostor/        # probe images that should NOT match (any wrong accept = FP)

Filename rule: participant id is token before first '_' (e.g. P001).

Usage (from py-backend with venv active):
  python research/run_folder_benchmark.py --dataset path/to/dataset --out research/output/attempts.csv
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from verification_metrics import (
    FACE_MATCH_TOLERANCE,
    VerificationAttempt,
    compare_1_to_n,
    compute_classification_metrics,
    encode_face_from_path,
    utc_now_iso,
    write_attempts_csv,
)


def parse_participant_id(filename: str) -> str:
    return filename.split("_")[0]


def load_gallery(gallery_dir: Path) -> Tuple[List[np.ndarray], List[str]]:
    encodings: List[np.ndarray] = []
    ids: List[str] = []
    for path in sorted(gallery_dir.glob("*")):
        if not path.is_file():
            continue
        enc, faces = encode_face_from_path(path)
        if enc is None:
            print(f"[skip] gallery {path.name}: faces_detected={faces}")
            continue
        encodings.append(enc)
        ids.append(parse_participant_id(path.name))
    return encodings, ids


def run_probe(
    probe_path: Path,
    ground_truth_should_match: bool,
    ground_truth_id: str,
    gallery_encodings: List[np.ndarray],
    gallery_ids: List[str],
) -> VerificationAttempt:
    t0 = time.perf_counter()
    enc, faces = encode_face_from_path(probe_path)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    if enc is None:
        return VerificationAttempt(
            timestamp=utc_now_iso(),
            endpoint="offline_benchmark",
            participant_id=ground_truth_id,
            predicted_id=None,
            ground_truth_should_match=ground_truth_should_match,
            predicted_match=False,
            confidence_score=None,
            threshold=FACE_MATCH_TOLERANCE,
            faces_detected=faces,
            lighting_condition=None,
            processing_time_ms=elapsed_ms,
            notes=f"probe={probe_path.name}",
        )

    idx, min_dist, _ = compare_1_to_n(enc, gallery_encodings)
    predicted_match = idx is not None
    predicted_id = gallery_ids[idx] if idx is not None else None

    return VerificationAttempt(
        timestamp=utc_now_iso(),
        endpoint="offline_benchmark",
        participant_id=ground_truth_id,
        predicted_id=predicted_id,
        ground_truth_should_match=ground_truth_should_match,
        predicted_match=predicted_match,
        confidence_score=min_dist,
        threshold=FACE_MATCH_TOLERANCE,
        faces_detected=faces,
        lighting_condition=None,
        processing_time_ms=elapsed_ms,
        notes=f"probe={probe_path.name}",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("research/output/attempts.csv"))
    args = parser.parse_args()

    gallery_dir = args.dataset / "gallery"
    genuine_dir = args.dataset / "probes" / "genuine"
    impostor_dir = args.dataset / "probes" / "impostor"

    gallery_encodings, gallery_ids = load_gallery(gallery_dir)
    if not gallery_encodings:
        raise SystemExit(f"No valid gallery images in {gallery_dir}")

    attempts: List[VerificationAttempt] = []

    if genuine_dir.is_dir():
        for probe in sorted(genuine_dir.glob("*")):
            if not probe.is_file():
                continue
            pid = parse_participant_id(probe.name)
            attempts.append(
                run_probe(probe, True, pid, gallery_encodings, gallery_ids)
            )

    if impostor_dir.is_dir():
        for probe in sorted(impostor_dir.glob("*")):
            if not probe.is_file():
                continue
            pid = parse_participant_id(probe.name)
            attempts.append(
                run_probe(probe, False, pid, gallery_encodings, gallery_ids)
            )

    write_attempts_csv(args.out, attempts)
    metrics = compute_classification_metrics(attempts)

    print("=== Benchmark complete ===")
    print(f"Gallery size: {len(gallery_ids)}")
    print(f"Attempts logged: {len(attempts)}")
    print(f"CSV: {args.out}")
    print("Metrics:", metrics)


if __name__ == "__main__":
    main()
