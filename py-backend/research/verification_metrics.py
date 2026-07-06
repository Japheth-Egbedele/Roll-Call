"""
Research utilities for face-verification metrics (FAR/FRR/confusion matrix).

Uses the same pipeline as face_attendance_backend.py:
  load_image -> HOG face_locations (default) -> 128-d embedding -> Euclidean distance

Env:
  FACE_MATCH_TOLERANCE (default 0.4)
  MONGO_URI (for export from verification_attempts collection)
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import face_recognition
import numpy as np

FACE_MATCH_TOLERANCE = float(os.getenv("FACE_MATCH_TOLERANCE", "0.4"))


@dataclass
class VerificationAttempt:
    timestamp: str
    endpoint: str
    participant_id: Optional[str]  # ground-truth identity if known
    predicted_id: Optional[str]
    ground_truth_should_match: Optional[bool]
    predicted_match: bool
    confidence_score: Optional[float]  # min Euclidean distance to gallery (lower = more similar)
    threshold: float
    faces_detected: int
    lighting_condition: Optional[str]
    processing_time_ms: float
    session_id: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode_face_from_bytes(file_bytes: bytes) -> Tuple[Optional[np.ndarray], int]:
    image = face_recognition.load_image_file(io.BytesIO(file_bytes))
    faces = face_recognition.face_encodings(image)
    if len(faces) != 1:
        return None, len(faces)
    return faces[0], 1


def encode_face_from_path(path: Path) -> Tuple[Optional[np.ndarray], int]:
    return encode_face_from_bytes(path.read_bytes())


def compare_1_to_n(
    probe_encoding: np.ndarray,
    gallery_encodings: Sequence[np.ndarray],
    tolerance: float = FACE_MATCH_TOLERANCE,
) -> Tuple[Optional[int], float, List[float]]:
    """
    Returns (best_match_index or None, min_distance, all_distances).
    Match rule: min_distance <= tolerance (same as compare_faces).
    """
    if not gallery_encodings:
        return None, float("inf"), []

    distances = face_recognition.face_distance(
        list(gallery_encodings),
        probe_encoding,
    ).tolist()
    best_idx = int(np.argmin(distances))
    min_dist = float(distances[best_idx])
    if min_dist <= tolerance:
        return best_idx, min_dist, distances
    return None, min_dist, distances


def compare_1_to_1(
    probe_encoding: np.ndarray,
    known_encoding: np.ndarray,
    tolerance: float = FACE_MATCH_TOLERANCE,
) -> Tuple[bool, float]:
    distance = float(
        face_recognition.face_distance([known_encoding], probe_encoding)[0]
    )
    return distance <= tolerance, distance


def compute_classification_metrics(
    attempts: Sequence[VerificationAttempt],
) -> Dict[str, Any]:
    """
    Binary verification metrics where ground_truth_should_match is known.

    TP: genuine accepted
    FN: genuine rejected (FRR numerator)
    FP: impostor accepted (FAR numerator)
    TN: impostor rejected
    """
    labeled = [a for a in attempts if a.ground_truth_should_match is not None]
    if not labeled:
        return {
            "error": "No labeled attempts (ground_truth_should_match required)",
            "n_attempts": len(attempts),
            "n_labeled": 0,
        }

    tp = fn = fp = tn = 0
    for a in labeled:
        gt = a.ground_truth_should_match
        pred = a.predicted_match
        if gt and pred:
            tp += 1
        elif gt and not pred:
            fn += 1
        elif not gt and pred:
            fp += 1
        else:
            tn += 1

    genuine = tp + fn
    impostor = fp + tn
    total = tp + tn + fp + fn

    return {
        "n_attempts": len(attempts),
        "n_labeled": len(labeled),
        "TP": tp,
        "TN": tn,
        "FP": fp,
        "FN": fn,
        "accuracy": (tp + tn) / total if total else None,
        "FRR": fn / genuine if genuine else None,
        "FAR": fp / impostor if impostor else None,
        "precision": tp / (tp + fp) if (tp + fp) else None,
        "recall": tp / genuine if genuine else None,
        "threshold": labeled[0].threshold if labeled else FACE_MATCH_TOLERANCE,
    }


def write_attempts_csv(path: Path, attempts: Sequence[VerificationAttempt]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [a.to_dict() for a in attempts]
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def export_mongo_attempts(
    mongo_uri: str,
    db_name: str = "attendance_system",
    collection: str = "verification_attempts",
    out_csv: Optional[Path] = None,
) -> Tuple[List[VerificationAttempt], Dict[str, Any]]:
    from pymongo import MongoClient

    client = MongoClient(mongo_uri)
    docs = list(client[db_name][collection].find({}).sort("timestamp", 1))
    attempts: List[VerificationAttempt] = []
    for d in docs:
        attempts.append(
            VerificationAttempt(
                timestamp=str(d.get("timestamp", "")),
                endpoint=str(d.get("endpoint", "")),
                participant_id=d.get("participant_id"),
                predicted_id=d.get("predicted_id"),
                ground_truth_should_match=d.get("ground_truth_should_match"),
                predicted_match=bool(d.get("predicted_match")),
                confidence_score=d.get("confidence_score"),
                threshold=float(d.get("threshold", FACE_MATCH_TOLERANCE)),
                faces_detected=int(d.get("faces_detected", 0)),
                lighting_condition=d.get("lighting_condition"),
                processing_time_ms=float(d.get("processing_time_ms", 0)),
                session_id=d.get("session_id"),
                notes=d.get("notes"),
            )
        )
    metrics = compute_classification_metrics(attempts)
    if out_csv:
        write_attempts_csv(out_csv, attempts)
    return attempts, metrics
