import io
import os
import time
import base64
import threading
import datetime
import traceback
import numpy as np
import cv2
import face_recognition
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo import MongoClient
from bson.objectid import ObjectId
from dotenv import load_dotenv
from pydantic import BaseModel

import cv2
import time

def open_camera(index=0, max_attempts=5, wait=0.5):
    """Try to open camera with retries and CAP_DSHOW backend."""
    cap = None
    for attempt in range(max_attempts):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)  # DirectShow backend
        if cap.isOpened():
            return cap
        else:
            cap.release()
            time.sleep(wait)
    raise RuntimeError(f"Cannot open camera index {index}")

# ----------------------
# --- ENV & CONFIG
# ----------------------
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
SERVER_CAMERA_INDEX = int(os.getenv("SERVER_CAMERA_INDEX", 0))
FACE_MATCH_TOLERANCE = float(os.getenv("FACE_MATCH_TOLERANCE", 0.5))

if not MONGO_URI:
    raise RuntimeError("MONGO_URI missing in .env")

# ----------------------
# --- APP INIT
# ----------------------
app = FastAPI(title="Face Recognition Attendance Backend")

# Allow frontend CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------
# --- DATABASE
# ----------------------
client = MongoClient(MONGO_URI)
db = client.attendance_system
students_col = db.students
lecturers_col = db.lecturers
sessions_col = db.sessions
attendance_col = db.attendance

# ----------------------
# --- GLOBAL SESSION TRACKER
# ----------------------
_running_sessions = {}
_running_lock = threading.Lock()

# ----------------------
# --- UTILS
# ----------------------
def now_utc():
    return datetime.datetime.utcnow()

def serialize_encoding(enc: np.ndarray):
    return enc.tolist()

def deserialize_encoding(blob):
    return np.array(blob, dtype=np.float64)

def decode_base64_image(img_str: str):
    """Decode base64 string, strip data URL if present"""
    if "," in img_str:
        img_str = img_str.split(",")[1]
    return base64.b64decode(img_str)

# ----------------------
# --- STUDENT ENROLLMENT
# ----------------------
@app.post("/students/enroll")
async def enroll_student(
    name: str = Body(...),
    matric_no: str = Body(...),
    file: UploadFile = File(...)
):
    try:
        content = await file.read()
        img = face_recognition.load_image_file(io.BytesIO(content))
        face_locs = face_recognition.face_locations(img)
        if not face_locs:
            return JSONResponse({"error": "No face detected in the image"}, status_code=400)

        encoding = face_recognition.face_encodings(img, face_locs)[0]
        res = students_col.insert_one({
            "name": name,
            "matric_no": matric_no,
            "face_encoding": serialize_encoding(encoding),
            "created_at": now_utc()
        })
        print(f"[STUDENT] Enrolled {name} ({matric_no}) ID={res.inserted_id}")
        return {"student_id": str(res.inserted_id)}
    except Exception:
        traceback.print_exc()
        return JSONResponse({"error": "Failed to enroll student"}, status_code=500)

# ----------------------
# --- LECTURER ENROLLMENT
# ----------------------
@app.post("/lecturers/enroll")
async def enroll_lecturer(
    name: str = Body(...),
    staff_id: str = Body(...),
    file: UploadFile = File(...)
):
    try:
        content = await file.read()
        img = face_recognition.load_image_file(io.BytesIO(content))
        face_locs = face_recognition.face_locations(img)
        if not face_locs:
            return {"error": "No face detected in the image"}

        encoding = face_recognition.face_encodings(img, face_locs)[0]
        res = lecturers_col.insert_one({
            "name": name,
            "staff_id": staff_id,
            "face_encoding": serialize_encoding(encoding),
            "created_at": now_utc()
        })
        print(f"[LECTURER] Enrolled {name} ({staff_id}) ID={res.inserted_id}")
        return {"lecturer_id": str(res.inserted_id)}
    except Exception:
        traceback.print_exc()
        return {"error": "Failed to enroll lecturer"}

# ----------------------
# --- LECTURER VERIFICATION
# ----------------------
@app.post("/lecturers/verify")
def verify_lecturer(image: dict = Body(...)):
    try:
        img_str = image.get("image", "")
        if not img_str:
            return {"verified": False, "error": "No image data received"}

        img_bytes = decode_base64_image(img_str)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img_np = np.array(img)
        face_encodings = face_recognition.face_encodings(img_np)
        if not face_encodings:
            return {"verified": False, "error": "No face detected"}

        face_encoding = face_encodings[0]
        lecturers = list(lecturers_col.find({}))
        if not lecturers:
            return {"verified": False, "error": "No lecturers enrolled"}

        min_distance = None
        best_match = None
        for lecturer in lecturers:
            lec_enc = np.array(lecturer["face_encoding"], dtype=np.float64)
            distance = face_recognition.face_distance([lec_enc], face_encoding)[0]
            if min_distance is None or distance < min_distance:
                min_distance = distance
                best_match = lecturer

        if min_distance is not None and min_distance <= FACE_MATCH_TOLERANCE:
            return {
                "verified": True,
                "lecturer_id": str(best_match["_id"]),
                "name": best_match["name"]
            }
        else:
            return {"verified": False, "error": "No matching lecturer found"}
    except Exception:
        traceback.print_exc()
        return {"verified": False, "error": "Error during lecturer verification"}

# ----------------------
# --- STUDENT SCAN DURING SESSION
# ----------------------
class StudentScan(BaseModel):
    image: str
    session_id: str
class QrScan(BaseModel):
    matric_no: str # The ID encoded in the QR code
    session_id: str
    audit_image: str = None # Optional base64 image for audit/snapping
@app.post("/sessions/scan")
async def scan_student(scan: StudentScan):
    try:
        if not scan.image:
            return JSONResponse({"detected": False, "error": "No image provided"}, status_code=400)

        # Decode base64 image
        img_bytes = decode_base64_image(scan.image)
        img_np = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        rgb = cv2.cvtColor(img_np, cv2.COLOR_BGR2RGB)
        face_encodings = face_recognition.face_encodings(rgb)

        if not face_encodings:
            return {"detected": False, "error": "No face detected"}

        students = list(students_col.find({}))
        for fe in face_encodings:
            for s in students:
                s_enc = deserialize_encoding(s["face_encoding"])
                if face_recognition.compare_faces([s_enc], fe, tolerance=FACE_MATCH_TOLERANCE)[0]:
                    attendance_col.update_one(
                        {"session_id": ObjectId(scan.session_id), "student_id": s["_id"]},
                        {"$setOnInsert": {"first_seen": now_utc(), "status": "present"},
                         "$set": {"last_seen": now_utc()}},
                        upsert=True
                    )
                    return {"detected": True, "name": s["name"], "matric_no": s["matric_no"]}

        return {"detected": False, "error": "No matching student found"}

    except Exception:
        traceback.print_exc()
        return {"detected": False, "error": "Error during student scan"}

# Paste this new endpoint after the existing /sessions/scan function:
@app.post("/sessions/scan_qr")
async def scan_student_qr(scan: QrScan):
    try:
        if not scan.matric_no:
            return JSONResponse({"detected": False, "error": "No matriculation number provided"}, status_code=400)

        # 1. Standardize the ID to lower case for robust lookup
        standardized_matric_no = scan.matric_no.lower()
        
        # 🎯 DIAGNOSTIC: Print the standardized ID
        print(f"[QR Backend] Received and Standardized ID for lookup: '{standardized_matric_no}'")

        # 2. Find the student using the standardized ID
        student = students_col.find_one({"matric_no": standardized_matric_no})

        if not student:
            # Check the database for the *exact* string you are using to enroll students
            print(f"[QR Scan Error] Standardized ID '{standardized_matric_no}' not found in MongoDB.") 
            return {"detected": False, "error": f"Student ID {scan.matric_no} not recognized"}

        # 3. Log attendance (using the found student's ID)
        now = now_utc()
        attendance_col.update_one(
            {"session_id": ObjectId(scan.session_id), "student_id": student["_id"]},
            {"$setOnInsert": {"first_seen": now, "status": "present_qr"},
             "$set": {"last_seen": now}},
            upsert=True
        )
        
        # ... (Audit image and return success)

        print(f"[QR Scan] Logged attendance for {student['matric_no']} ({student['name']})")
        return {"detected": True, "name": student["name"], "matric_no": student["matric_no"]}

    except Exception:
        traceback.print_exc()
        return {"detected": False, "error": "Error during QR student scan"}
# ----------------------
# --- SESSION MANAGEMENT
# ----------------------
def _session_loop(session_id_obj):
    """Background loop to process live camera feed"""
    session_id = str(session_id_obj)
    print(f"[SESSION {session_id}] Starting session...")

    try:
        students = list(students_col.find({}))
        student_encs = [deserialize_encoding(s["face_encoding"]) for s in students]
        student_ids = [s["_id"] for s in students]

        lecturers = list(lecturers_col.find({}))
        lecturer_encs = [deserialize_encoding(l["face_encoding"]) for l in lecturers]
        lecturer_ids = [l["_id"] for l in lecturers]

        all_encs = student_encs + lecturer_encs
        all_ids = student_ids + lecturer_ids

        def get_type_and_id(matched_id):
            if matched_id in student_ids:
                return "student", matched_id
            if matched_id in lecturer_ids:
                return "lecturer", matched_id
            return "unknown", None

        cap = cv2.VideoCapture(SERVER_CAMERA_INDEX)
        if not cap.isOpened():
            raise RuntimeError("Camera not available")

        last_seen = {}
        identified_lecturer = None

        while True:
            with _running_lock:
                if not _running_sessions.get(session_id, {}).get("active"):
                    break

            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue

            rgb = frame[:, :, ::-1]
            face_encodings_frame = face_recognition.face_encodings(rgb, face_recognition.face_locations(rgb))
            now = now_utc()

            for fe in face_encodings_frame:
                if not all_encs:
                    continue
                distances = face_recognition.face_distance(all_encs, fe)
                best_idx = int(np.argmin(distances))

                if distances[best_idx] <= FACE_MATCH_TOLERANCE:
                    matched_id = all_ids[best_idx]
                    entity_type, entity_id = get_type_and_id(matched_id)
                    key = str(entity_id)

                    if key in last_seen and (now - last_seen[key]).total_seconds() < 30:
                        continue
                    last_seen[key] = now

                    if entity_type == "student":
                        attendance_col.update_one(
                            {"session_id": session_id_obj, "student_id": entity_id},
                            {"$setOnInsert": {"first_seen": now, "status": "present"},
                             "$set": {"last_seen": now}},
                            upsert=True
                        )
                    elif entity_type == "lecturer" and identified_lecturer is None:
                        identified_lecturer = entity_id
                        sessions_col.update_one(
                            {"_id": session_id_obj},
                            {"$set": {"lecturer_id": identified_lecturer, "lecturer_seen_at": now}}
                        )
                        print(f"[SESSION {session_id}] Lecturer {identified_lecturer} identified.")

            cv2.waitKey(1)

    except Exception:
        traceback.print_exc()
        print(f"[SESSION {session_id}] Thread crashed!")

    finally:
        try:
            cap.release()
        except:
            pass
        print(f"[SESSION {session_id}] Stopped.")

# ----------------------
# --- SESSION MODELS
# ----------------------
class SessionStart(BaseModel):
    lecturer_id: str
    title: str
    code: str

class StopSession(BaseModel):
    session_id: str

# ----------------------
# --- SESSION ROUTES
# ----------------------

# Start a new session
@app.post("/sessions/start")
def start_session(session: SessionStart):
    session_doc = {
        "start_time": now_utc(),
        "active": True,
        "lecturer_id": ObjectId(session.lecturer_id),
        "title": session.title,
        "code": session.code
    }
    res = sessions_col.insert_one(session_doc)
    session_id = str(res.inserted_id)

    with _running_lock:
        _running_sessions[session_id] = {"thread": None, "active": True}

    t = threading.Thread(target=_session_loop, args=(res.inserted_id,), daemon=True)
    _running_sessions[session_id]["thread"] = t
    t.start()

    print(f"[API] Session started: {session_id}")
    return {"session_id": session_id}

# Stop an active session
@app.post("/sessions/stop")
def stop_session(data: StopSession):
    session_id = data.session_id
    with _running_lock:
        if session_id in _running_sessions:
            _running_sessions[session_id]["active"] = False

    sessions_col.update_one(
        {"_id": ObjectId(session_id)},
        {"$set": {"active": False, "end_time": now_utc()}}
    )
    print(f"[API] Session stopped: {session_id}")
    return {"ok": True}

# List currently running sessions
@app.get("/sessions/running")
def list_running_sessions():
    with _running_lock:
        return list(_running_sessions.keys())

# ----------------------
# --- SESSION HISTORY HELPER
# ----------------------
from bson import ObjectId

def serialize_session(session):
    """Convert ObjectId fields to strings."""
    session["_id"] = str(session["_id"])
    if "lecturer_id" in session and isinstance(session["lecturer_id"], ObjectId):
        session["lecturer_id"] = str(session["lecturer_id"])
    return session

# ----------------------
# --- SESSION HISTORY
# ----------------------
@app.get("/sessions/history")
async def get_all_sessions():
    sessions = list(sessions_col.find({}).sort("created_at", -1))
    sessions = [serialize_session(s) for s in sessions]
    return {"sessions": sessions}

# ----------------------
# --- SESSION DETAILS
# ----------------------
@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    if session_id == "history":
        return {"error": "Invalid session id"}
    try:
        session = sessions_col.find_one({"_id": ObjectId(session_id)})
        if not session:
            return {"error": "Session not found"}

        lecturer_name = None
        if "lecturer_id" in session:
            lec = lecturers_col.find_one({"_id": session["lecturer_id"]})
            if lec:
                lecturer_name = lec["name"]

        return {
            "session_id": str(session["_id"]),
            "lecturer_name": lecturer_name,
            "course_title": session.get("title", "--"),
            "start_time": session.get("start_time"),
            "end_time": session.get("end_time"),
            "active": session.get("active", False)
        }
    except Exception as e:
        return {"error": str(e)}

# Get attendance for a specific session
@app.get("/sessions/{session_id}/attendance")
def get_session_attendance(session_id: str):
    try:
        records = list(attendance_col.find({"session_id": ObjectId(session_id)}))
        result = []
        for r in records:
            student = students_col.find_one({"_id": r["student_id"]})
            if student:
                result.append({
                    "name": student["name"],
                    "matric_no": student["matric_no"],
                    "first_seen": r.get("first_seen"),
                    "last_seen": r.get("last_seen"),
                    "status": r.get("status")
                })
        return result
    except Exception as e:
        return {"error": str(e)}
