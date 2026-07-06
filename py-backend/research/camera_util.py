"""Windows-friendly webcam open (avoids MSMF 'can't grab frame' errors)."""

from __future__ import annotations

import sys

import cv2


def open_camera(index: int = 0) -> cv2.VideoCapture:
  backends = []
  if sys.platform == "win32":
    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, 0]
  else:
    backends = [0]

  last_err = None
  for backend in backends:
    cap = cv2.VideoCapture(index, backend) if backend else cv2.VideoCapture(index)
    if cap.isOpened():
      return cap
    last_err = f"index={index} backend={backend}"
    cap.release()

  raise SystemExit(
    "Cannot open webcam. Close browser tabs using the camera (lecturer.html, Zoom, Teams), "
    f"then retry. ({last_err})"
  )


def capture_jpeg(index: int = 0, warmup_frames: int = 15) -> bytes:
  cap = open_camera(index)
  try:
    for _ in range(warmup_frames):
      cap.read()
    ok, frame = cap.read()
    if not ok or frame is None:
      raise SystemExit(
        "Webcam capture failed. Another app is probably using the camera — close it and retry."
      )
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
      raise SystemExit("Could not encode webcam frame.")
    return buf.tobytes()
  finally:
    cap.release()
