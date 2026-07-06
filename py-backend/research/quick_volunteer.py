"""
Automated volunteer capture — one person, guided poses, reliable timing.

Usage:
  python research/seed_lab.py
  python research/quick_volunteer.py --id P01 --name "Your Name"

Controls during capture:
  SPACE = take photo when you're ready
  R     = retake current pose
  Q     = quit
"""

from __future__ import annotations

import argparse
import io
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import face_recognition
import numpy as np
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

from camera_util import open_camera

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verification_metrics import compute_classification_metrics, write_attempts_csv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

API = os.getenv("RESEARCH_API", "http://127.0.0.1:8000")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
STAFF_ID = "STAFF_LAB"
COURSE_NAME = "Research Lab Session"
WINDOW = "Roll-Call Capture  |  SPACE=photo  R=retake  Q=quit"

POSES = [
    ("center", "Look straight at the camera", "center"),
    ("left", "Turn your head LEFT", "left"),
    ("right", "Turn your head RIGHT", "right"),
    ("close", "Move CLOSER to the camera", "close"),
    ("far", "Move BACK from the camera", "far"),
]


def say(msg: str) -> None:
    print(f"\n>>> {msg}")


def face_count_in_jpeg(jpeg: bytes) -> int:
    image = face_recognition.load_image_file(io.BytesIO(jpeg))
    return len(face_recognition.face_encodings(image))


def frame_to_jpeg(frame: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


def draw_pose_icon(frame: np.ndarray, pose: str) -> None:
    h, w = frame.shape[:2]
    cx, cy = w // 2, h // 2
    color = (0, 220, 255)
    thick = 3

    if pose == "center":
        cv2.ellipse(frame, (cx, cy - 20), (90, 110), 0, 0, 360, color, thick)
        cv2.circle(frame, (cx - 35, cy - 40), 8, color, -1)
        cv2.circle(frame, (cx + 35, cy - 40), 8, color, -1)
        cv2.ellipse(frame, (cx, cy + 10), (40, 20), 0, 0, 180, color, thick)

    elif pose == "left":
        cv2.arrowedLine(frame, (cx + 120, cy), (cx - 80, cy), color, thick + 2, tipLength=0.35)
        cv2.putText(frame, "LEFT", (cx - 160, cy - 30), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

    elif pose == "right":
        cv2.arrowedLine(frame, (cx - 120, cy), (cx + 80, cy), color, thick + 2, tipLength=0.35)
        cv2.putText(frame, "RIGHT", (cx + 40, cy - 30), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)

    elif pose == "close":
        cv2.rectangle(frame, (cx - 140, cy - 160), (cx + 140, cy + 160), color, thick)
        cv2.putText(frame, "+", (cx - 25, cy + 20), cv2.FONT_HERSHEY_SIMPLEX, 3.0, color, 4)

    elif pose == "far":
        cv2.rectangle(frame, (cx - 60, cy - 70), (cx + 60, cy + 70), color, thick)
        cv2.putText(frame, "-", (cx - 20, cy + 15), cv2.FONT_HERSHEY_SIMPLEX, 3.0, color, 4)


def draw_hud(frame: np.ndarray, pose_idx: int, total: int, label: str, hint: str, faces: int) -> None:
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 100), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)

    cv2.putText(frame, f"Pose {pose_idx}/{total}: {label.upper()}", (20, 38),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.putText(frame, hint, (20, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (200, 200, 200), 2)

    face_ok = faces == 1
    face_color = (0, 200, 0) if face_ok else (0, 0, 255)
    face_msg = "1 face OK - press SPACE" if face_ok else f"faces: {faces} - adjust position"
    cv2.putText(frame, face_msg, (20, h - 50), cv2.FONT_HERSHEY_SIMPLEX, 0.75, face_color, 2)
    cv2.putText(frame, "SPACE = capture   R = retake   Q = quit", (20, h - 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1)


def warmup_camera(cap: cv2.VideoCapture, frames: int = 45) -> None:
    say("Warming up camera — stay still...")
    for _ in range(frames):
        cap.read()
        time.sleep(0.03)


def interactive_capture_pose(
    cap: cv2.VideoCapture,
    pose_key: str,
    hint: str,
    pose_idx: int,
    total: int,
) -> bytes:
    say(f"Pose {pose_idx}/{total}: {hint}")
    say("When the icon looks right and you see '1 face OK', press SPACE.")

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError("Lost webcam stream. Close other apps using the camera.")

        preview = frame.copy()
        draw_pose_icon(preview, pose_key)

        small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
        try:
            faces = face_count_in_jpeg(frame_to_jpeg(small))
        except Exception:
            faces = 0

        draw_hud(preview, pose_idx, total, pose_key, hint, faces)
        cv2.imshow(WINDOW, preview)
        key = cv2.waitKey(30) & 0xFF

        if key == ord("q"):
            raise SystemExit("Aborted by user.")
        if key in (ord(" "), 13):
            jpeg = frame_to_jpeg(frame)
            faces_full = face_count_in_jpeg(jpeg)
            if faces_full != 1:
                say(f"Need exactly 1 face, got {faces_full}. Adjust and press SPACE again (or R).")
                continue
            flash = preview.copy()
            cv2.putText(flash, "CAPTURED!", (preview.shape[1] // 2 - 120, preview.shape[0] // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 255, 0), 3)
            cv2.imshow(WINDOW, flash)
            cv2.waitKey(600)
            print(f"   captured: {pose_key}")
            return jpeg
        if key == ord("r"):
            say("Retake — adjust position...")


def api_enroll(participant_id: str, name: str, jpeg: bytes) -> dict:
    r = requests.post(
        f"{API}/students/enroll",
        data={"name": name, "matric_no": participant_id},
        files={"file": ("enroll.jpg", jpeg, "image/jpeg")},
        timeout=120,
    )
    if not r.ok:
        detail = r.text
        try:
            detail = r.json()
        except Exception:
            pass
        raise SystemExit(f"Enroll failed ({r.status_code}): {detail}")
    return r.json()


def get_course_id() -> str:
    client = MongoClient(MONGO_URI)
    course = client["attendance_system"].courses.find_one({"name": COURSE_NAME})
    if not course:
        raise SystemExit("Run: python research/seed_lab.py")
    return course["id"]


def api_start_session(course_id: str) -> str:
    r = requests.post(
        f"{API}/attendance/start_session",
        params={"course_id": course_id, "lecturer_id": STAFF_ID, "mode": "face"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["session_id"]


def api_face_verify(session_id: str, jpeg: bytes) -> dict:
    r = requests.post(
        f"{API}/attendance/face_recognize/{session_id}",
        files={"file": ("probe.jpg", jpeg, "image/jpeg")},
        timeout=120,
    )
    if not r.ok:
        return {"status": "Error", "message": r.text[:200]}
    return r.json()


def api_end_session(session_id: str) -> None:
    requests.post(f"{API}/attendance/end_session/{session_id}", timeout=30)


def save_shots(base: Path, participant_id: str, shots: dict[str, bytes]) -> None:
    gallery = base / "gallery"
    genuine = base / "probes" / "genuine"
    gallery.mkdir(parents=True, exist_ok=True)
    genuine.mkdir(parents=True, exist_ok=True)
    (gallery / f"{participant_id}_enroll.jpg").write_bytes(shots["center"])
    for label, jpeg in shots.items():
        (genuine / f"{participant_id}_{label}.jpg").write_bytes(jpeg)


def pull_latency_stats() -> None:
    client = MongoClient(MONGO_URI)
    rows = list(client["attendance_system"].verification_attempts.find({}))
    if not rows:
        print("No verification_attempts in Mongo yet.")
        return
    enroll_ms = [r["processing_time_ms"] for r in rows if r.get("endpoint") == "/students/enroll"]
    verify_ms = [
        r["processing_time_ms"]
        for r in rows
        if r.get("endpoint") == "/attendance/face_recognize"
    ]
    print("\n=== Latency (server processing_time_ms) ===")
    if enroll_ms:
        print(f"  Enroll:  n={len(enroll_ms)} mean={statistics.mean(enroll_ms):.0f}ms median={statistics.median(enroll_ms):.0f}ms")
    if verify_ms:
        print(f"  Verify:  n={len(verify_ms)} mean={statistics.mean(verify_ms):.0f}ms median={statistics.median(verify_ms):.0f}ms")


def main() -> None:
    parser = argparse.ArgumentParser(description="Guided webcam volunteer capture")
    parser.add_argument("--id", required=True, help="Participant ID e.g. P01")
    parser.add_argument("--name", required=True, help="Full name")
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[2] / "dataset")
    args = parser.parse_args()

    try:
        requests.get(f"{API}/courses", timeout=5)
    except Exception as e:
        raise SystemExit(f"Start the API first: uvicorn face_attendance_backend:app --reload\n{e}")

    cap = open_camera(0)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)

    try:
        say(f"Hi {args.name} ({args.id}). One window will stay open for all poses.")
        warmup_camera(cap)

        shots: dict[str, bytes] = {}
        total = len(POSES)
        for i, (key, hint, icon) in enumerate(POSES, start=1):
            shots[key] = interactive_capture_pose(cap, icon, hint, i, total)

        say("Live enroll via API...")
        api_enroll(args.id, args.name, shots["center"])

        course_id = get_course_id()
        say("Live face verification (one API call per pose)...")
        session_id = api_start_session(course_id)

        for key, _, _ in POSES:
            result = api_face_verify(session_id, shots[key])
            print(f"   verify [{key}]: {result.get('status')} {result.get('name', '')} — {result.get('message', '')}")

        api_end_session(session_id)
        save_shots(args.dataset, args.id, shots)
        say(f"Saved images under {args.dataset}")
        pull_latency_stats()

        try:
            from run_folder_benchmark import load_gallery, run_probe

            gallery_enc, gallery_ids = load_gallery(args.dataset / "gallery")
            attempts = [
                run_probe(args.dataset / "probes" / "genuine" / f"{args.id}_{key}.jpg", True, args.id, gallery_enc, gallery_ids)
                for key, _, _ in POSES
            ]
            metrics = compute_classification_metrics(attempts)
            print("\n=== Offline genuine-match metrics ===")
            print(metrics)
            out = Path(__file__).resolve().parent / "output" / f"{args.id}_attempts.csv"
            write_attempts_csv(out, attempts)
            print(f"CSV: {out}")
        except Exception as e:
            print("Offline metrics skip:", e)

        print("\nDone. Export Mongo:")
        print("  python research/export_attempts.py --out research/output/live_attempts.csv")
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
