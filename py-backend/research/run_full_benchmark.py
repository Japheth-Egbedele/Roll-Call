"""
Full-dataset benchmark with automatic cross-participant impostor trials.

Expects quick_volunteer output layout:
  dataset/gallery/P01_enroll.jpg
  dataset/probes/genuine/P01_center.jpg, P01_left.jpg, ...

Genuine trial: probe owner should be identified as themselves (1:N).
Impostor trial: probe from person A tested 1:1 against each other gallery member B (A≠B).
  False accept = probe A incorrectly matches B's enrollment encoding.

Usage:
  python research/run_full_benchmark.py --dataset ../dataset --tau 0.4
  python research/threshold_sweep.py --dataset ../dataset
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
    compare_1_to_1,
    compare_1_to_n,
    compute_classification_metrics,
    encode_face_from_path,
    utc_now_iso,
    write_attempts_csv,
)


def parse_id_and_pose(filename: str) -> Tuple[str, str]:
    stem = Path(filename).stem
    parts = stem.split("_", 1)
    pid = parts[0]
    pose = parts[1] if len(parts) > 1 else "unknown"
    return pid, pose


def load_gallery(gallery_dir: Path) -> Tuple[List[np.ndarray], List[str]]:
    encodings: List[np.ndarray] = []
    ids: List[str] = []
    for path in sorted(gallery_dir.glob("*")):
        if not path.is_file():
            continue
        enc, faces = encode_face_from_path(path)
        if enc is None:
            print(f"[skip] gallery {path.name}: faces={faces}")
            continue
        encodings.append(enc)
        ids.append(parse_id_and_pose(path.name)[0])
    return encodings, ids


def genuine_attempt(
    probe_path: Path,
    owner_id: str,
    pose: str,
    gallery_encodings: List[np.ndarray],
    gallery_ids: List[str],
    tolerance: float,
) -> VerificationAttempt:
    t0 = time.perf_counter()
    enc, faces = encode_face_from_path(probe_path)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    if enc is None:
        return VerificationAttempt(
            timestamp=utc_now_iso(),
            endpoint="offline_genuine",
            participant_id=owner_id,
            predicted_id=None,
            ground_truth_should_match=True,
            predicted_match=False,
            confidence_score=None,
            threshold=tolerance,
            faces_detected=faces,
            lighting_condition=pose,
            processing_time_ms=elapsed_ms,
            notes=probe_path.name,
        )

    idx, min_dist, _ = compare_1_to_n(enc, gallery_encodings, tolerance)
    predicted_id = gallery_ids[idx] if idx is not None else None
    correct = predicted_id == owner_id

    return VerificationAttempt(
        timestamp=utc_now_iso(),
        endpoint="offline_genuine",
        participant_id=owner_id,
        predicted_id=predicted_id,
        ground_truth_should_match=True,
        predicted_match=correct,
        confidence_score=min_dist,
        threshold=tolerance,
        faces_detected=faces,
        lighting_condition=pose,
        processing_time_ms=elapsed_ms,
        notes=probe_path.name,
    )


def impostor_attempt(
    probe_path: Path,
    owner_id: str,
    target_id: str,
    pose: str,
    probe_enc: np.ndarray,
    target_enc: np.ndarray,
    tolerance: float,
    elapsed_ms: float,
) -> VerificationAttempt:
    accepted, dist = compare_1_to_1(probe_enc, target_enc, tolerance)
    return VerificationAttempt(
        timestamp=utc_now_iso(),
        endpoint="offline_impostor",
        participant_id=owner_id,
        predicted_id=target_id if accepted else None,
        ground_truth_should_match=False,
        predicted_match=accepted,
        confidence_score=dist,
        threshold=tolerance,
        faces_detected=1,
        lighting_condition=pose,
        processing_time_ms=elapsed_ms,
        notes=f"{probe_path.name}->vs_{target_id}",
    )


def run_benchmark(dataset: Path, tolerance: float) -> Tuple[List[VerificationAttempt], Dict]:
    gallery_dir = dataset / "gallery"
    genuine_dir = dataset / "probes" / "genuine"

    gallery_encodings, gallery_ids = load_gallery(gallery_dir)
    if len(gallery_encodings) < 1:
        raise SystemExit(f"No gallery in {gallery_dir}")

    id_to_encoding = dict(zip(gallery_ids, gallery_encodings))
    attempts: List[VerificationAttempt] = []

    probe_paths = sorted(genuine_dir.glob("*")) if genuine_dir.is_dir() else []
    for probe in probe_paths:
        if not probe.is_file():
            continue
        owner_id, pose = parse_id_and_pose(probe.name)

        attempts.append(
            genuine_attempt(probe, owner_id, pose, gallery_encodings, gallery_ids, tolerance)
        )

        if len(gallery_ids) < 2:
            continue

        enc, faces = encode_face_from_path(probe)
        if enc is None:
            continue
        t0 = time.perf_counter()
        for target_id, target_enc in id_to_encoding.items():
            if target_id == owner_id:
                continue
            attempts.append(
                impostor_attempt(
                    probe, owner_id, target_id, pose, enc, target_enc, tolerance,
                    (time.perf_counter() - t0) * 1000,
                )
            )

    metrics = compute_classification_metrics(attempts)
    metrics["threshold"] = tolerance
    metrics["gallery_size"] = len(gallery_ids)
    metrics["participants"] = sorted(set(gallery_ids))
    return attempts, metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[2] / "dataset")
    parser.add_argument("--tau", type=float, default=FACE_MATCH_TOLERANCE)
    parser.add_argument("--out", type=Path, default=Path("research/output/full_benchmark.csv"))
    args = parser.parse_args()

    attempts, metrics = run_benchmark(args.dataset, args.tau)
    write_attempts_csv(args.out, attempts)

    print("=== Full benchmark ===")
    print(f"Gallery: {metrics.get('gallery_size')} participants")
    print(f"Threshold τ={args.tau}")
    print(f"Attempts: {metrics.get('n_attempts')} (genuine + auto impostor)")
    print(f"  FAR={metrics.get('FAR')}  FRR={metrics.get('FRR')}  acc={metrics.get('accuracy')}")
    print(f"CSV: {args.out}")
    print("Metrics:", {k: metrics[k] for k in metrics if k not in ("participants",)})


if __name__ == "__main__":
    main()
