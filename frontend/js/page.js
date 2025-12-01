// ===========================
// GLOBAL VARIABLES
// ===========================
let verificationTimer, lecturerScanInterval, lecturerVerified = false;
let studentScanInterval;
const MAX_ATTEMPTS = 15;
let currentLecturerId = null;
let currentLecturerName = null;
let currentScanMode = 'FACE'; // Initial mode
let qrCodeScanner = null;     // Holds the html5-qrcode instance

// ===========================
// BACKEND API HELPERS
// ===========================
async function postJSON(url, body) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

async function getJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

// ===========================
// WEBCAM FUNCTIONS
// ===========================
function initWebcam(videoId) {
    const video = document.getElementById(videoId);
    if (!video) return;
    stopWebcam(videoId);
    navigator.mediaDevices.getUserMedia({ video: true })
        .then(stream => { video.srcObject = stream; video.play(); })
        .catch(err => {
            console.error(`Camera error on #${videoId}:`, err);
            const statusEl = video.closest('.page-content, .camera-panel')?.querySelector('.feedback, #lecturerStatus, #endScanStatus');
            if (statusEl) statusEl.innerHTML = '<span style="color:red;font-weight:bold;"><i class="bx bx-error-circle"></i> Camera access denied.</span>';
        });
}

function stopWebcam(videoId) {
    const video = document.getElementById(videoId);
    if (video?.srcObject) {
        // This is the core line that releases the camera
        video.srcObject.getTracks().forEach(track => track.stop()); 
        video.srcObject = null;
    }
}

// ===========================
// LECTURER VERIFICATION
// ===========================
function startLecturerScan(videoId, onVerified) {
    const video = document.getElementById(videoId);
    if (!video) return;
    lecturerVerified = false;
    let attempts = 0;

    if (lecturerScanInterval) clearInterval(lecturerScanInterval);

    lecturerScanInterval = setInterval(async () => {
        if (video.readyState < 2 || lecturerVerified) return;
        attempts++;

        const canvas = document.createElement('canvas');
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);

        const base64Image = canvas.toDataURL('image/jpeg');
        const lecturerStatus = document.getElementById('lecturerStatus') || document.getElementById('endScanStatus');

        try {
            const data = await postJSON("http://localhost:8000/lecturers/verify", { image: base64Image });

            if (data.verified && data.lecturer_id) {
                lecturerVerified = true;
                clearInterval(lecturerScanInterval);
                lecturerStatus.innerHTML = `<span style="color:green;font-weight:bold;"><i class="bx bx-check-circle"></i> Verified: ${data.name}</span>`;
                if (onVerified) onVerified(data.lecturer_id, data.name);
            } else {
                lecturerStatus.innerHTML = `<span style="color:orange;"><i class="bx bx-loader-alt bx-spin"></i> Scanning... (${attempts}/${MAX_ATTEMPTS})</span>`;
                if (attempts >= MAX_ATTEMPTS) {
                    clearInterval(lecturerScanInterval);
                    lecturerStatus.innerHTML = `<span style="color:red;font-weight:bold;"><i class="bx bx-error-circle"></i> No lecturer detected.</span>`;
                }
            }
        } catch (err) {
            console.error(err);
            clearInterval(lecturerScanInterval);
            lecturerStatus.innerHTML = `<span style="color:red;font-weight:bold;"><i class="bx bx-error-circle"></i> Verification failed.</span>`;
        }
    }, 1000);
}

// ===========================
// STUDENT SCANNING
// ===========================
function startStudentScan(videoId) {
    if (currentScanMode === 'QR') {
        // If we are in QR mode, skip the face scan interval setup
        return startQrScan(); 
    }
    
    // Ensure we are in FACE mode setup:
    stopQrScan(); // Stop QR scanner if it was somehow running
    const video = document.getElementById(videoId);
    if (!video) return;

    // Show face elements, hide QR elements
    document.getElementById('studentScanVideo').style.display = 'block';
    document.getElementById('qr-reader').style.display = 'none'; // Ensure QR reader is hidden
    
    // Start webcam and interval for Face Scan
    initWebcam(videoId); 

    if (studentScanInterval) clearInterval(studentScanInterval);

    studentScanInterval = setInterval(async () => {
        if (video.readyState < 2) return;

        const canvas = document.createElement('canvas');
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);

        const base64Image = canvas.toDataURL('image/jpeg');
        const currentSession = JSON.parse(localStorage.getItem('currentSession'));
        if (!currentSession) return;

        try {
            const data = await postJSON("http://localhost:8000/sessions/scan", {
                image: base64Image,
                session_id: currentSession.session_id
            });


            if (data.detected) {
                // Pass method: 'FACE' for face scan logging
                markStudentAttendance({ id: data.matric_no, name: data.name, method: 'FACE' }); 
            }
        } catch (err) {
            console.error("Student scan failed:", err);
        }
    }, 1000);
}

function markStudentAttendance(student) {
    const session = JSON.parse(localStorage.getItem('currentSession'));
    if (!session.attendance.some(s => s.id === student.id)) {
        session.attendance.push(student);
        localStorage.setItem('currentSession', JSON.stringify(session));

        const li = document.createElement('li');
        // Display the method used (e.g., 'QR', 'FACE')
        li.textContent = `${student.id} (${student.name}) - [${student.method || 'FACE'}]`; 
        document.getElementById('attendanceList').prepend(li); // prepend for latest at top

        const attendeeCountEl = document.getElementById('attendeeCount');
        attendeeCountEl.textContent = session.attendance.length;
    }
}

// ===========================
// QR CODE SCANNING FUNCTIONS
// ===========================

// 1. Function to handle successful QR scan
function onQrScanSuccess(decodedText) {
    if (decodedText) {
        console.log("QR Code Scanned Value:", decodedText); // Debugging line
        // Pause scanning briefly to prevent repeated logs of the same code
        if (qrCodeScanner) qrCodeScanner.pause(true); 

        // Trigger the attendance marking process with the QR data
        processQrAttendance(decodedText); 
        
        // Resume scanning after a brief delay
setTimeout(() => {
            if (qrCodeScanner) {
                // Attempt to resume, handle potential error if camera is busy/closed
                qrCodeScanner.resume().catch(err => {
                    console.warn("Failed to resume QR scanner, probably waiting for user action:", err);
                });
            }
        }, 3000); 
    }
}

// 2. API call for QR attendance (sends ID and an audit snap)
async function processQrAttendance(idValue) {
    const currentSession = JSON.parse(localStorage.getItem('currentSession'));
    // ✅ Apply the trim fix here:
    const trimmedId = idValue.trim();
    if (!currentSession) return;
    
    // 1. Capture Image for Audit (Scan & Snap)
    // We capture the image from the video stream managed by the QR reader itself
    const videoElement = document.getElementById('qr-reader').querySelector('video');
    let base64Image = null;
    let scanStatus = document.getElementById('scanStatus');

    // FIX: Use readyState >= 2 for better compatibility when capturing image
    if (videoElement && videoElement.readyState >= 2) { 
        const canvas = document.createElement('canvas');
        canvas.width = videoElement.videoWidth;
        canvas.height = videoElement.videoHeight;
        canvas.getContext('2d').drawImage(videoElement, 0, 0, canvas.width, canvas.height);
        base64Image = canvas.toDataURL('image/jpeg');
    }
    
if (scanStatus) scanStatus.innerHTML = `<span style="color:blue;"><i class="bx bx-loader-alt bx-spin"></i> Processing QR Code for ID: ${trimmedId}...</span>`;
    try {
        // Calls the new backend endpoint /sessions/scan_qr
// Use trimmedId in the API call:
    const data = await postJSON("http://localhost:8000/sessions/scan_qr", {
        matric_no: trimmedId,
            session_id: currentSession.session_id,
            audit_image: base64Image // Send the snapped photo for review later
        });

        if (data.detected) {
            markStudentAttendance({ id: data.matric_no, name: data.name, method: 'QR' });
            if (scanStatus) scanStatus.innerHTML = `<span style="color:green;"><i class="bx bx-check-circle"></i> QR Success: ${data.name}</span>`;
        } else {
            if (scanStatus) scanStatus.innerHTML = `<span style="color:red;"><i class="bx bx-error-circle"></i> QR Error: Student ID not recognized.</span>`;
        }
    } catch (err) {
        console.error("QR scan failed:", err);
        if (scanStatus) scanStatus.innerHTML = `<span style="color:red;"><i class="bx bx-error-circle"></i> Network Error during QR scan.</span>`;
    }
}

// 3. Function to start the QR scanner
function startQrScan() {
    // 1. Stop Face Scan/Webcam
    // FIX: Clear the interval first, then null the variable
    if (studentScanInterval) clearInterval(studentScanInterval);
    studentScanInterval = null;
    
    stopWebcam('studentScanVideo');
    
    // If scanner instance already exists, clear it before re-rendering
    if(qrCodeScanner) {
        qrCodeScanner.clear().catch(e => console.error("Error clearing QR scanner:", e));
        qrCodeScanner = null;
    }

// 2. Hide face elements, show QR elements
    document.getElementById('studentScanVideo').style.display = 'none';
    const qrReaderEl = document.getElementById('qr-reader');
    qrReaderEl.style.display = 'block';
    
    // ADD THIS LINE TO CLEAR ANY PREVIOUS RENDERED ELEMENTS
    qrReaderEl.innerHTML = '';
    // 3. Initialize and start the QR scanner
    qrCodeScanner = new Html5QrcodeScanner(
        "qr-reader", 
        { 
            fps: 10, 
            qrbox: 250, 
            disableFlip: false 
        }, 
        true // verbose logging
    );
    // FIX: Pass camera configuration to render()
    // It will prompt the user to select the camera.
    qrCodeScanner.render(onQrScanSuccess, (error) => {
         console.error("QR Scanner Initialization Error:", error);
         document.getElementById('scanStatus').innerHTML = '<span style="color:red;font-weight:bold;"><i class="bx bx-error-circle"></i> Failed to start camera for QR scan.</span>';
    });
    // Stops working here
    document.getElementById('scanStatus').textContent = "Scanning in QR Mode...";
}

// 4. Function to stop QR scanning
function stopQrScan() {
    if (qrCodeScanner) {
        qrCodeScanner.clear().catch(e => console.error("Error clearing QR scanner:", e));
        qrCodeScanner = null;
    }
    document.getElementById('qr-reader').style.display = 'none';
    document.getElementById('qr-reader').style.display = 'none';
    document.getElementById('scanStatus').textContent = "Scan ready. Waiting for student...";
}

// ===========================
// SESSION MANAGEMENT
// ===========================
async function createNewSession(title, code) {
    try {
        const res = await postJSON('http://localhost:8000/sessions/start', {
            lecturer_id: currentLecturerId,
            title,
            code
        });
        const session_id = res.session_id; // returned by backend
        const session = {
            lecturer_id: currentLecturerId,
            lecturer_name: currentLecturerName,
            title,
            code,
            session_id,
            attendance: []
        };
        localStorage.setItem('currentSession', JSON.stringify(session));
        return session_id;
    } catch (err) {
        console.error('Failed to start session:', err);
        alert('Error starting session');
    }
}

async function endCurrentSession() {
    clearInterval(studentScanInterval);
    const currentSession = JSON.parse(localStorage.getItem('currentSession'));
    if (!currentSession) return;

    try {
        await postJSON('http://localhost:8000/sessions/stop', { session_id: currentSession.session_id });
    } catch (err) {
        console.error('Failed to stop session:', err);
    }

    const allSessions = JSON.parse(localStorage.getItem('allSessions') || '[]');
    allSessions.push(currentSession);
    localStorage.setItem('allSessions', JSON.stringify(allSessions));
    localStorage.removeItem('currentSession');
}

// ===========================
// UPDATE SESSION INFO DISPLAY
// ===========================
function updateSessionInfo() {
    const session = JSON.parse(localStorage.getItem('currentSession'));
    if (session) {
        document.getElementById('sessionCourse').textContent = `${session.code} - ${session.title}`;
        document.getElementById('sessionLecturer').textContent = session.lecturer_name || 'Unknown';
    }
}

// ===========================
// HISTORY UTILITIES
// ===========================
let allSessions = []; // store all sessions globally after loading

function renderSessionList(sessions) {
    const list = document.getElementById('historyList');
    list.innerHTML = '';
    sessions.forEach(sess => {
        const li = document.createElement('li');
        li.innerHTML = `<strong>${sess.title}</strong> 
                        <button onclick="viewSession('${sess._id}')">View Attendance</button>`;
        list.appendChild(li);
    });
}

async function loadSessionHistory() {
    const list = document.getElementById('historyList');
    list.innerHTML = '';

    try {
        const data = await getJSON('http://localhost:8000/sessions/history');
        allSessions = data.sessions; // store globally

        renderSessionList(allSessions);
    } catch (err) {
        console.error('Failed to load session history', err);
        list.innerHTML = '<li style="color:red;">Failed to load sessions.</li>';
    }
}

// ===========================
// HISTORY DETAILS FUNCTION
// ===========================
window.viewSession = async function(session_id) {
    showPage('historyDetails'); // This now calls the function defined below
    try {
        const sessionData = await getJSON(`http://localhost:8000/sessions/${session_id}`);
        // Use course_title here
        document.getElementById('sessionDetailsInfo').textContent = `${sessionData.course_title} - ${sessionData.lecturer_name || 'Unknown'}`;
        
        const attendance = await getJSON(`http://localhost:8000/sessions/${session_id}/attendance`);
        const list = document.getElementById('historyAttendanceList');
        list.innerHTML = '';
        attendance.forEach(s => {
            const li = document.createElement('li');
            
            // Display the correct method based on backend data
            let method = 'Face ID';
            if (s.status === 'present_qr') {
                method = 'QR Code';
            }
            
            li.textContent = `${s.matric_no} (${s.name}) [Method: ${method}]`;
            list.appendChild(li);
        });
    } catch (err) {
        console.error('Failed to load session details', err);
    }
}

// ===========================
// SPA NAVIGATION
// ===========================
const pages = document.querySelectorAll('.page'); // Moved outside DOMContentLoaded

function showPage(id) { // Moved outside DOMContentLoaded
    clearTimeout(verificationTimer);
    pages.forEach(p => p.classList.remove('active'));
    const target = document.getElementById(id);
    if (target) target.classList.add('active');

    // Stop all webcams before switching page
    ['scanVideo','studentScanVideo','endScanVideo','enrollVideo','video'].forEach(stopWebcam);
    
    // Stop student scan interval and QR scanner if running
    clearInterval(studentScanInterval);
    if (id !== 'attendance') {
        stopQrScan();
    }
    
    if(id==='scan') {
        initWebcam('scanVideo');
        const status = document.getElementById('lecturerStatus');
        if(status) status.innerHTML='<span style="color:blue;"><i class="bx bx-loader-alt bx-spin"></i> Verifying Lecturer...</span>';
        startLecturerScan('scanVideo', (lecturer_id, name) => {
            currentLecturerId = lecturer_id;
            currentLecturerName = name;
            showPage('newSession');
        });
    }
    else if(id==='newSession') {
        const btn = document.getElementById('createSessionBtn');
        if(btn) btn.onclick = async () => {
            const title = document.getElementById('courseTitle').value.trim();
            const code = document.getElementById('courseCode').value.trim();
            if(!title||!code){ alert("Enter course title and code"); return; }
            await createNewSession(title, code);
            updateSessionInfo();
            showPage('attendance');
        };
    }
    else if(id==='attendance') {
        // Set the initial state for the attendance page
        const toggleBtn = document.getElementById('toggleScanModeBtn');
        if (currentScanMode === 'QR') {
            if(toggleBtn) toggleBtn.textContent = 'Switch to Face Scan';
            startQrScan();
        } else {
            if(toggleBtn) toggleBtn.textContent = 'Switch to QR Code Scan';
            initWebcam('studentScanVideo');
            startStudentScan('studentScanVideo');
        }

        document.getElementById('attendanceList').innerHTML='';
        document.getElementById('attendeeCount').textContent='0';
        updateSessionInfo();
    }
    else if(id==='endScan') {
        initWebcam('endScanVideo');
        const status = document.getElementById('endScanStatus');
        if(status) status.innerHTML='<span style="color:blue;"><i class="bx bx-loader-alt bx-spin"></i> Verifying Lecturer to End Session...</span>';
        startLecturerScan('endScanVideo', async () => {
            await endCurrentSession();
            if(status) status.innerHTML='<span style="color:green;font-weight:bold;"><i class="bx bx-check-circle"></i> Session Saved!</span>';
            setTimeout(()=>showPage('landing'),2000);
        });
    }
    else if(id==='history') {
        loadSessionHistory();
    }
}


// ===========================
// EVENT LISTENERS (Runs ONCE on load)
// ===========================
document.addEventListener('DOMContentLoaded', function () {
    
    // --- TOGGLE BUTTON LISTENER (Fixed Position) ---
// Inside the document.addEventListener('DOMContentLoaded', function () { ... });
    
    // --- TOGGLE BUTTON LISTENER ---
    const toggleBtn = document.getElementById('toggleScanModeBtn');
    if (toggleBtn) {
        // ... (other setup for toggleBtn)
        
        toggleBtn.addEventListener('click', () => {
            if (currentScanMode === 'FACE') {
                // Switching from FACE to QR Mode
                currentScanMode = 'QR';
                toggleBtn.textContent = 'Switch to Face Scan';
                
                // CRITICAL ADDITIONS: Ensure Face Scan resources are killed
                clearInterval(studentScanInterval);
                studentScanInterval = null;
                stopWebcam('studentScanVideo'); 

                // *** CHANGE THIS LINE: Introduce a 500ms delay ***
                setTimeout(startQrScan, 500); 
            } else {
                // Switching from QR back to FACE Mode
                currentScanMode = 'FACE';
                toggleBtn.textContent = 'Switch to QR Code Scan';
                stopQrScan();
                startStudentScan('studentScanVideo'); 
            }
        });
    }
    // ----------------------------------------------

    // Main navigation buttons
    document.getElementById('startBtn')?.addEventListener('click',()=>showPage('scan'));
    document.getElementById('enrollNavBtn')?.addEventListener('click',()=>showPage('enroll'));
    document.getElementById('endSessionBtn')?.addEventListener('click',()=>showPage('endScan'));
    document.getElementById('historyBtn')?.addEventListener('click',()=>showPage('history'));
    
    // Back buttons
    document.querySelectorAll('.backBtn').forEach(btn=>{
        btn.addEventListener('click',e=>{
            e.preventDefault();
            showPage(btn.dataset.target);
        });
    });

    // Theme toggle logic (remains unchanged)
    const themeToggle = document.getElementById("themeToggle");
    themeToggle?.addEventListener('click',()=>{
        const body=document.body;
        if(body.classList.contains('light')){
            body.classList.replace('light','dark');
            themeToggle.classList.replace('bx-sun','bx-moon');
            localStorage.setItem('theme','dark');
        } else {
            body.classList.replace('dark','light');
            themeToggle.classList.replace('bx-moon','bx-sun');
            localStorage.setItem('theme','light');
        }
    });
    const savedTheme = localStorage.getItem('theme')||'light';
    document.body.classList.remove('light','dark');
    document.body.classList.add(savedTheme);
    themeToggle?.classList.toggle('bx-moon', savedTheme==='dark');
    themeToggle?.classList.toggle('bx-sun', savedTheme==='light');

    // Search sessions
    document.getElementById('searchBtn')?.addEventListener('click', () => {
        const query = document.getElementById('sessionSearch').value.toLowerCase();
        const filtered = allSessions.filter(sess =>
            sess.title.toLowerCase().includes(query) ||
            (sess.lecturer_name || '').toLowerCase().includes(query)
        );
        renderSessionList(filtered);
    });
});