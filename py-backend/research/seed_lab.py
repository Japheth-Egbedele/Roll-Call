"""
One-time lab setup: approved lecturer + course for automated benchmark runs.

Usage (webcam required once for lecturer face):
  cd py-backend
  python research/seed_lab.py
"""

from __future__ import annotations

import io
import os
import sys
import uuid
from pathlib import Path

import cv2
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

from camera_util import capture_jpeg

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

API = os.getenv("RESEARCH_API", "http://127.0.0.1:8000")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
STAFF_ID = "STAFF_LAB"
COURSE_NAME = "Research Lab Session"


def enroll_lecturer(name: str, staff_id: str, jpeg: bytes) -> None:
    files = {"file": ("lecturer.jpg", jpeg, "image/jpeg")}
    data = {"name": name, "staff_id": staff_id}
    r = requests.post(f"{API}/lecturers/enroll", data=data, files=files, timeout=60)
    if r.status_code not in (200, 201):
        print("Lecturer enroll:", r.status_code, r.text[:200])


def main() -> None:
    try:
        requests.get(f"{API}/courses", timeout=5)
    except Exception as e:
        raise SystemExit(f"API not reachable at {API}. Start uvicorn first.\n{e}")

    client = MongoClient(MONGO_URI)
    db = client["attendance_system"]

    if not db.lecturers.find_one({"staff_id": STAFF_ID}):
        print("Capturing lecturer face (look at camera)...")
        jpeg = capture_jpeg()
        enroll_lecturer("Lab Admin", STAFF_ID, jpeg)
    else:
        print("Lecturer already exists:", STAFF_ID)

    db.lecturers.update_one({"staff_id": STAFF_ID}, {"$set": {"approved": True}})

    course = db.courses.find_one({"name": COURSE_NAME})
    if not course:
        course_id = str(uuid.uuid4())
        db.courses.insert_one(
            {"id": course_id, "name": COURSE_NAME, "lecturer_id": STAFF_ID}
        )
        db.lecturers.update_one(
            {"staff_id": STAFF_ID},
            {"$addToSet": {"courses": {"id": course_id, "name": COURSE_NAME}}},
        )
        print("Created course:", course_id)
    else:
        course_id = course["id"]
        print("Course exists:", course_id)

    print("\nLab ready.")
    print(f"  lecturer_id = {STAFF_ID}")
    print(f"  course_id   = {course_id}")


if __name__ == "__main__":
    main()
