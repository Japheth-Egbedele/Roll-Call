from dotenv import load_dotenv
load_dotenv() 

import os
import io
import uuid
import base64
import asyncio # <--- CRITICAL IMPORT ADDED
from fastapi import FastAPI, UploadFile, Form, WebSocket, WebSocketDisconnect, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Dict
from pymongo import MongoClient
import face_recognition
from bson.objectid import ObjectId
import numpy as np
import datetime
from PIL import Image
from fastapi import HTTPException


# ------------------ CONFIG ------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
FACE_MATCH_TOLERANCE = float(os.getenv("FACE_MATCH_TOLERANCE", 0.4))

client = MongoClient(MONGO_URI)
db = client['attendance_system']

app = FastAPI()

# Custom JSON encoder to handle MongoDB's ObjectId
app.json_encoders = {
    ObjectId: str 
}

# Allow CORS for frontend testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# ------------------ MODELS ------------------
class CourseModel(BaseModel):
    name: str
    lecturer_id: str = None 

# ------------------ GLOBALS ------------------
active_sessions: Dict[str, Dict] = {}
websockets: Dict[str, List[WebSocket]] = {}

# ------------------ UTILS ------------------
def encode_face(file_bytes):
    """Reads image bytes → returns face encoding or error."""
    image = face_recognition.load_image_file(io.BytesIO(file_bytes))
    faces = face_recognition.face_encodings(image)
    if len(faces) != 1:
        return None, len(faces)
    return faces[0].tolist(), 1


def verify_face(file_bytes, known_encoding):
    """Matches uploaded image with known face encoding."""
    image = face_recognition.load_image_file(io.BytesIO(file_bytes))
    faces = face_recognition.face_encodings(image)
    if len(faces) != 1:
        return False, len(faces)
    match = face_recognition.compare_faces(
        [np.array(known_encoding)],
        faces[0],
        tolerance=FACE_MATCH_TOLERANCE
    )
    return match[0], 1

# 🛑 NEW HELPER: Face comparison against many known faces
def recognize_face(live_face_bytes, known_encodings):
    """
    Compares the face in the live image against a list of known encodings.
    Returns the index of the best match or None, and the number of faces found.
    """
    image = face_recognition.load_image_file(io.BytesIO(live_face_bytes))
    live_faces = face_recognition.face_encodings(image)
    
    faces_found = len(live_faces)
    if faces_found != 1:
        return None, faces_found

    # Compare the single live face against all known faces
    # face_recognition.compare_faces returns a list of True/False
    matches = face_recognition.compare_faces(
        [np.array(e) for e in known_encodings],
        live_faces[0],
        tolerance=FACE_MATCH_TOLERANCE
    )
    
    # Find the index of the first True match
    try:
        match_index = matches.index(True)
        return match_index, 1
    except ValueError:
        # No match found
        return None, 1
    

# ------------------ UTILS (Final Version) ------------------
def clean_mongo_doc(doc: dict) -> dict:
    """Recursively converts ObjectIds and datetimes to strings/ISO format for JSON serialization."""
    if not doc:
        return {}
    
    cleaned = {} 
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            cleaned[key] = str(value)
        elif isinstance(value, datetime.datetime):
            cleaned[key] = value.isoformat()
        elif isinstance(value, dict):
            cleaned[key] = clean_mongo_doc(value)
        elif isinstance(value, list):
            cleaned[key] = [clean_mongo_doc(item) if isinstance(item, dict) else item for item in value]
        else:
            cleaned[key] = value
            
    if 'face_encoding' in cleaned:
        del cleaned['face_encoding']

    return cleaned

def load_all_known_faces():
    """Loads all student face encodings and associated data from the database."""
    all_students = list(db.students.find({}))
    known_face_encodings = [s.get("face_encoding") for s in all_students if s.get("face_encoding")]
    student_details = [{
        "matric_no": s["matric_no"], 
        "name": s["name"]
    } for s in all_students if s.get("face_encoding")]
    return known_face_encodings, student_details

# 🔥 CRITICAL NEW LOGIC: Identify student from a live face image
def identify_face_and_confirm(face_image_bytes: bytes, session_id: str):
    """
    Scans the image for a face and attempts to match it against all known students.
    Returns matched student details or None.
    """
    
    try:
        known_encodings, student_details = load_all_known_faces()

        # Use the multi-face comparison function
        match_index, faces_found = recognize_face(face_image_bytes, known_encodings)
        
        if faces_found != 1:
            return None, "Face Error: Found 0 or multiple faces."
            
        if match_index is not None:
            # We found a match! Get the student details
            matched_student = student_details[match_index]
            return matched_student, "Confirmed"
        else:
            return None, "Rejected: No match found."

    except Exception as e:
        print(f"ERROR in identify_face_and_confirm: {e}")
        return None, f"Server Error: {e}"
        

# --- WEBSOCKET HELPER (CRITICAL FOR RELIABLE BROADCAST) ---
async def broadcast_attendance(session_id, attendance_list):
    """Handles sending the attendance list to all connected websockets for a session."""
    if session_id in websockets:
        send_tasks = []
        for ws in websockets[session_id]:
            try:
                # Append the send coroutine to the list of tasks
                send_tasks.append(ws.send_json(attendance_list))
            except Exception:
                pass # Connection likely closed
        
        # Wait for all send tasks to complete before proceeding
        if send_tasks:
            # Use asyncio.gather to ensure all messages are sent concurrently
            await asyncio.gather(*send_tasks, return_exceptions=True)

# ------------------ STUDENT ENROLLMENT ------------------
@app.post("/students/enroll")
async def enroll_student(name: str = Form(...), matric_no: str = Form(...), file: UploadFile = None):
    # ... (Enrollment logic unchanged) ...
    file_bytes = await file.read()
    encoding, faces_found = encode_face(file_bytes)

    if faces_found == 0:
        return JSONResponse({"error": "No face detected"}, status_code=400)
    if faces_found > 1:
        return JSONResponse({"error": "Multiple faces detected"}, status_code=400)

    db.students.update_one(
        {"matric_no": matric_no},
        {"$set": {
            "name": name,
            "matric_no": matric_no,
            "face_encoding": encoding
        }},
        upsert=True
    )
    return {"matric_no": matric_no, "name": name, "status": "enrolled"}

# ------------------ LECTURER ENROLLMENT ------------------
@app.post("/lecturers/enroll")
async def enroll_lecturer(name: str = Form(...), staff_id: str = Form(...), file: UploadFile = None):
    # ... (Enrollment logic unchanged) ...
    file_bytes = await file.read()
    encoding, faces_found = encode_face(file_bytes)

    if faces_found == 0:
        return JSONResponse({"error": "No face detected"}, status_code=400)
    if faces_found > 1:
        return JSONResponse({"error": "Multiple faces detected"}, status_code=400)

    db.lecturers.update_one(
        {"staff_id": staff_id},
        {"$set": {
            "name": name,
            "staff_id": staff_id,
            "face_encoding": encoding,
            "approved": False,
            "courses": []
        }},
        upsert=True
    )

    return {"staff_id": staff_id, "status": "pending_approval"}


# ------------------ LECTURER AUTHENTICATION ------------------
@app.post("/lecturers/authenticate")
async def authenticate_lecturer(file: UploadFile = None):
    # ... (Authentication logic unchanged) ...
    file_bytes = await file.read()

    approved_lecturers = list(db.lecturers.find({"approved": True}, {"_id": 0}))
    for lec in approved_lecturers:
        match, faces_found = verify_face(file_bytes, lec["face_encoding"])

        if faces_found != 1:
            continue

        if match:
            return {
                "staff_id": lec["staff_id"],
                "name": lec["name"],
                "courses": lec.get("courses", [])
            }

    return JSONResponse({"error": "Face not recognized OR not approved"}, status_code=401)


# ------------------ ADMIN PANEL & COURSE MANAGEMENT (Unchanged) ------------------
@app.get("/lecturers/pending")
async def get_pending_lecturers():
    pending = list(db.lecturers.find({"approved": False}))
    return [clean_mongo_doc(doc) for doc in pending]


@app.post("/lecturers/approve/{staff_id}")
async def approve_lecturer(staff_id: str):
    result = db.lecturers.update_one({"staff_id": staff_id}, {"$set": {"approved": True}})
    if result.matched_count == 0:
        return JSONResponse({"error": "Lecturer not found"}, status_code=404)
    return {"staff_id": staff_id, "status": "approved"}


@app.get("/lecturers/all")
async def get_approved_lecturers():
    lecturers = list(db.lecturers.find({"approved": True})) 
    return [clean_mongo_doc(doc) for doc in lecturers]


@app.put("/lecturers/{staff_id}")
async def update_lecturer(staff_id: str, new_data: dict):
    update_fields = {}
    if 'name' in new_data:
        update_fields['name'] = new_data['name']
    if 'staff_id' in new_data and new_data['staff_id'] != staff_id:
        update_fields['staff_id'] = new_data['staff_id']
        
    if not update_fields:
        return JSONResponse({"error": "No data provided for update"}, status_code=400)

    result = db.lecturers.update_one({"staff_id": staff_id}, {"$set": update_fields})
    if result.matched_count == 0:
        return JSONResponse({"error": "Lecturer not found"}, status_code=404)
    return {"staff_id": staff_id, "status": "updated"}


@app.delete("/lecturers/{staff_id}")
async def delete_lecturer(staff_id: str):
    db.courses.update_many(
        {"lecturer_id": staff_id},
        {"$set": {"lecturer_id": None}}
    )

    result = db.lecturers.delete_one({"staff_id": staff_id})
    if result.deleted_count == 0:
        return JSONResponse({"error": "Lecturer not found"}, status_code=404)
    
    return {"staff_id": staff_id, "status": "deleted"}


@app.post("/courses")
async def create_course(course: CourseModel):
    course_id = str(uuid.uuid4())

    doc = {
        "id": course_id,
        "name": course.name,
        "lecturer_id": course.lecturer_id
    }
    db.courses.insert_one(doc)

    if course.lecturer_id:
        db.lecturers.update_one(
            {"staff_id": course.lecturer_id},
            {"$addToSet": {"courses": {"id": course_id, "name": course.name}}}
        )

    return {"id": course_id, "name": course.name, "lecturer_id": course.lecturer_id}


@app.get("/courses")
async def get_courses():
    courses = list(db.courses.find({}))
    return [clean_mongo_doc(doc) for doc in courses]


@app.put("/courses/{course_id}")
async def update_course(course_id: str, course: CourseModel):

    old_course = db.courses.find_one({"id": course_id})
    old_lecturer = old_course.get("lecturer_id") if old_course else None
    
    new_course_data = {"name": course.name, "lecturer_id": course.lecturer_id}

    db.courses.update_one(
        {"id": course_id},
        {"$set": new_course_data}
    )

    if old_lecturer:
        db.lecturers.update_one(
            {"staff_id": old_lecturer},
            {"$pull": {"courses": {"id": course_id}}}
        )

    if course.lecturer_id:
        db.lecturers.update_one(
            {"staff_id": course.lecturer_id},
            {"$addToSet": {"courses": {"id": course_id, "name": course.name}}}
        )

    return {"status": "updated"}


@app.delete("/courses/{course_id}")
async def delete_course(course_id: str):
    db.lecturers.update_many(
        {},
        {"$pull": {"courses": {"id": course_id}}}
    )

    db.courses.delete_one({"id": course_id})
    return {"status": "deleted"}


@app.get("/admin/attendance/detailed_history")
async def get_detailed_attendance_history():
    
    # --- MongoDB Aggregation Pipeline ---
    pipeline = [
        # 1. Join with the 'students' collection based on matric_no (UNCHANGED)
        {
            "$lookup": {
                "from": "students",
                "localField": "matric_no",
                "foreignField": "matric_no",
                "as": "student_info"
            }
        },
        # 2. Deconstruct the student_info array field (UNCHANGED)
        {
            "$unwind": {
                "path": "$student_info",
                "preserveNullAndEmptyArrays": True
            }
        },
        
        # Conversion to ObjectId 
        {
            "$addFields": {
                "courseObjectId": {
                    "$cond": {
                        # Check if course_id is a non-empty string and 24 characters long
                        "if": { "$eq": [ { "$strLenCP": "$course_id" }, 24 ] }, 
                        # If TRUE, convert it
                        "then": { "$toObjectId": "$course_id" },
                        # If FALSE (empty, short, or invalid), use a placeholder (e.g., null)
                        "else": None 
                    }
                }
            }
        },
        
        # 3. Join with the 'courses' collection based on the new courseObjectId field
        {
            "$lookup": {
                "from": "courses",
                "localField": "courseObjectId",
                "foreignField": "_id",
                "as": "course_info"
            }
        },
        # 4. Deconstruct the course_info array field (UNCHANGED)
        {
            "$unwind": {
                "path": "$course_info",
                "preserveNullAndEmptyArrays": True
            }
        },
        
        # 5. Project (shape) the final output document (UNCHANGED)
        {
            "$project": {
                "_id": 0,
                "session_id": 1,
                "matric_no": 1,
                "status": 1,
                "timestamp": 1,
                "student_name": "$student_info.name", 
                "course_name": "$course_info.name",
                "lecturer_id": 1
            }
        }
    ]

    history = list(db.attendance_records.aggregate(pipeline))
    return history


# ------------------ ATTENDANCE SYSTEM ------------------
@app.post("/attendance/start_session")
async def start_session(course_id: str, lecturer_id: str, mode: str):
    session_id = str(uuid.uuid4())
    
#  Store in memory (for fast lookup) ---
    attendance_list = [] 
    course_doc = db.courses.find_one({"id": course_id})
    lecturer_doc = db.lecturers.find_one({"staff_id": lecturer_id})
    course_name = course_doc.get("name") if course_doc else "Unknown Course"
    lecturer_name = lecturer_doc.get("name") if lecturer_doc else "Unknown Lecturer"

    active_sessions[session_id] = {
        "course_id": course_id,
        "course_name": course_name,
        "lecturer_id": lecturer_id,
        "lecturer_name": lecturer_name,
        "attendance": attendance_list, 
        "mode": mode
    }
    websockets[session_id] = []
    
    # Save to MongoDB for Persistence & Mode Switching ---
    db.attendance_sessions.insert_one({
        "session_id": session_id, 
        "course_id": course_id,
        "lecturer_id": lecturer_id,
        "start_time": datetime.datetime.utcnow(),
        "mode": mode,
        "is_active": True # Required by the switch_mode query
    })
    
    return {"session_id": session_id, "course_name": course_name, "attendance_list": attendance_list, "mode": mode}

# Model to define the expected body for switching mode
class SwitchModeRequest(BaseModel):
    new_mode: str # Expects "qr" or "face"

# 🛑 CRITICAL FIX: Ensure the path is exactly /attendance/switch_mode/{session_id}
@app.post("/attendance/switch_mode/{session_id}")
async def switch_attendance_mode(session_id: str, request: SwitchModeRequest):
    """
    Switches the attendance capture mode for an active session.
    """
    new_mode = request.new_mode.lower()
    
    if new_mode not in ["qr", "face"]:
        raise HTTPException(status_code=400, detail="Invalid mode. Must be 'qr' or 'face'.")

    # 1. Update the session record in the database
    result = db.attendance_sessions.update_one(
        {"session_id": session_id, "is_active": True},
        {"$set": {"mode": new_mode}}
    )

    if result.matched_count == 0:
        # NOTE: This means the session ID exists but is either not active or incorrect.
        raise HTTPException(status_code=404, detail="Active session not found or invalid ID.")
    
    # 2. Update the in-memory state as well! (Crucial for live operation)
    if session_id in active_sessions:
        active_sessions[session_id]["mode"] = new_mode

    # 3. Return confirmation
    return {"message": f"Session {session_id} mode successfully switched to {new_mode.upper()}.", "new_mode": new_mode}

# --- CORRECTED ENDPOINT: QR-ONLY ATTENDANCE MODE ---
@app.post("/attendance/qr_confirm/{session_id}/{matric_no}")
async def qr_confirm_attendance(session_id: str, matric_no: str):
    if session_id not in active_sessions:
        return JSONResponse({"error": "Session not active"}, status_code=400)
    
    session = active_sessions[session_id]
    
    if session.get("mode") != "qr":
        return JSONResponse({"error": "Session is running in Face Scan mode."}, status_code=400)
    
    student = db.students.find_one({"matric_no": matric_no})
    if not student:
        return JSONResponse({"error": "Student not found"}, status_code=404)

    record = next((r for r in session["attendance"] if r["matric_no"] == matric_no), None)
    
    if record and record["status"] == "Confirmed":
        await broadcast_attendance(session_id, session["attendance"])
        await asyncio.sleep(0.01) 
        return {"status": "Confirmed", "name": student.get("name"), "message": "Attendance already confirmed."}
    
    # If not confirmed, add the record
    if not record:
        new_record = {
            "matric_no": matric_no,
            "name": student.get("name"),
            "status": "Confirmed" 
        }
        session["attendance"].append(new_record)
        
    # Standardize the broadcast
    await broadcast_attendance(session_id, session["attendance"])
    
    # CRITICAL FIX: Ensure broadcast completes before HTTP response
    await asyncio.sleep(0.01) 

    return {"status": "Confirmed", "name": student.get("name"), "message": "Attendance confirmed via QR scan."}


# 🛑 NEW ENDPOINT: FACE RECOGNITION
@app.post("/attendance/face_recognize/{session_id}")
async def face_recognize_attendance(session_id: str, file: UploadFile = File(...)):
    if session_id not in active_sessions:
        return JSONResponse({"error": "Session not active"}, status_code=400)
    
    session = active_sessions[session_id]
    
    if session.get("mode") != "face":
        return JSONResponse({"error": "Session is running in QR Scan mode."}, status_code=400)

    # 1. Read the image file
    try:
        file_bytes = await file.read()
    except Exception as e:
        return JSONResponse({"error": f"Failed to read image file: {e}"}, status_code=400)
    
    # 2. Identify the student from the image (heavy lifting)
    # NOTE: You must ensure 'identify_face_and_confirm' is correctly defined elsewhere
    matched_student, status_msg = identify_face_and_confirm(file_bytes, session_id) 

    if not matched_student:
        # Rejects the image if no face or no match found
        print(f"❌ BLIND SCAN FAILED: {status_msg}")
        return {"status": "Rejected", "message": status_msg}

    # Student was matched successfully
    matric_no = matched_student["matric_no"]
    student_name = matched_student["name"]
    
    # 3. Check if already confirmed
    record = next((r for r in session["attendance"] if r["matric_no"] == matric_no), None)
    
    if record and record["status"] == "Confirmed":
        # Silent success if already confirmed
        await broadcast_attendance(session_id, session["attendance"])
        await asyncio.sleep(0.01) 
        return {"status": "Confirmed", "name": student_name, "message": "Attendance already confirmed."}
    
    # 4. Confirm attendance and broadcast
    if status_msg == "Confirmed":
        new_record = {
            "matric_no": matric_no,
            "name": student_name,
            "status": "Confirmed"
        }
        session["attendance"].append(new_record)
        
        print(f"✅ BLIND SCAN CONFIRMED: Student {student_name} ({matric_no}) added to session {session_id[:8]}...")

        await broadcast_attendance(session_id, session["attendance"])
        await asyncio.sleep(0.01) 
        
        return {"status": "Confirmed", "name": student_name, "message": "Attendance confirmed via blind face scan."}
    else:
        # Should be unreachable if matched_student is not None, but included for safety
        print(f"❌ BLIND SCAN FAILED: Verification failed for {student_name}. Status: {status_msg}")
        return {"status": "Rejected", "message": status_msg}


# MODIFIED ENDPOINT: Save Confirmed ONLY 
@app.post("/attendance/end_session/{session_id}")
async def end_session(session_id: str):
    if session_id not in active_sessions:
        return JSONResponse({"error": "Session not active"}, status_code=400)
    session = active_sessions[session_id]
    records_to_save = 0
    
    for record in session["attendance"]:
        
        if record["status"] == "Confirmed":
            db.attendance_records.insert_one({
                "session_id": session_id,
                "course_id": session["course_id"],
                "course_name": session["course_name"],
                "lecturer_name": session["lecturer_name"],
                "student_name": record["name"],
                "matric_no": record["matric_no"],
                "status": record["status"], 
                "timestamp": datetime.datetime.utcnow() 
            })
            records_to_save += 1

    # 💡 CRITICAL FIX: Update DB session status to inactive
    db.attendance_sessions.update_one(
        {"session_id": session_id},
        {"$set": {"is_active": False, "end_time": datetime.datetime.utcnow()}}
    )
    
    del active_sessions[session_id]
    if session_id in websockets:
        del websockets[session_id]

    return {"status": "Session ended", "records_saved": records_to_save}


@app.get("/attendance/history")
async def attendance_history():
    records = list(db.attendance_records.find({}))
    return [clean_mongo_doc(r) for r in records]


@app.get("/attendance/summary")
async def attendance_summary():
    pipeline = [
        {"$group": {
            "_id": {
                "session_id": "$session_id",
                "course_id": "$course_id",
                "course_name": "$course_name", 
                "lecturer_name": "$lecturer_name",
            },
            "total_present": {"$sum": 1}, 
            "last_session_time": {"$max": "$timestamp"}
        }},
        {"$project": {
            "_id": 0,
            "session_id": "$_id.session_id",
            "course_id": "$_id.course_id",
            "course_name": "$_id.course_name",
            "lecturer_name": "$_id.lecturer_name",
            "total_present": 1,
            "last_session_time": 1
        }},
        {"$sort": {"last_session_time": -1}}
    ]

    summary = list(db.attendance_records.aggregate(pipeline))
    
    return [clean_mongo_doc(item) for item in summary]

# ------------------ WEBSOCKET ------------------
@app.websocket("/attendance/ws/{session_id}")
async def ws_attendance(websocket: WebSocket, session_id: str):
    await websocket.accept()

    if session_id in active_sessions:
        if session_id not in websockets:
            websockets[session_id] = []
        websockets[session_id].append(websocket)
        
        await websocket.send_json(active_sessions[session_id]["attendance"])

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if session_id in websockets and websocket in websockets[session_id]:
            websockets[session_id].remove(websocket)
        print(f"WebSocket closed for session: {session_id}")
