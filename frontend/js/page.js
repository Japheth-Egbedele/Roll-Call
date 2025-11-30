// ===========================
// GLOBAL VARIABLES
// ===========================
let verificationTimer, lecturerScanInterval, lecturerVerified = false;
let studentScanInterval;
const MAX_ATTEMPTS = 15;
let currentLecturerId = null;
let currentLecturerName = null;

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
    const video = document.getElementById(videoId);
    if (!video) return;

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
                markStudentAttendance({ id: data.matric_no, name: data.name });
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
        li.textContent = `${student.id} (${student.name})`;
        document.getElementById('attendanceList').appendChild(li);

        const attendeeCountEl = document.getElementById('attendeeCount');
        attendeeCountEl.textContent = session.attendance.length;
    }
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
    // LOAD SESSION HISTORY
    // ===========================
    async function loadSessionHistory() {
        const list = document.getElementById('historyList');
        list.innerHTML = '';

        try {
            const data = await getJSON('http://localhost:8000/sessions/history'); // fetch from backend
            const sessions = data.sessions; // extract sessions array

            sessions.forEach((sess) => {
                const li = document.createElement('li');
                // Backend returns _id and title
                li.innerHTML = `<strong>${sess.title}</strong> 
                                <button onclick="viewSession('${sess._id}')">View Attendance</button>`;
                list.appendChild(li);
            });
        } catch (err) {
            console.error('Failed to load session history', err);
            list.innerHTML = '<li style="color:red;">Failed to load sessions.</li>';
        }
    }

    // ===========================
    // SPA NAVIGATION
    // ===========================
    document.addEventListener('DOMContentLoaded', function () {
        const pages = document.querySelectorAll('.page');

        function showPage(id) {
            clearTimeout(verificationTimer);
            pages.forEach(p => p.classList.remove('active'));
            const target = document.getElementById(id);
            if (target) target.classList.add('active');

            ['scanVideo','studentScanVideo','endScanVideo','enrollVideo','video'].forEach(stopWebcam);

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
                initWebcam('studentScanVideo');
                startStudentScan('studentScanVideo');

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

        document.getElementById('startBtn')?.addEventListener('click',()=>showPage('scan'));
        document.getElementById('enrollNavBtn')?.addEventListener('click',()=>showPage('enroll'));
        document.getElementById('endSessionBtn')?.addEventListener('click',()=>showPage('endScan'));
        document.getElementById('historyBtn')?.addEventListener('click',()=>showPage('history'));
        document.querySelectorAll('.backBtn').forEach(btn=>{
            btn.addEventListener('click',e=>{
                e.preventDefault();
                showPage(btn.dataset.target);
            });
        });

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

        // View past session
    window.viewSession = async function(session_id) {
        showPage('historyDetails');
        try {
            const sessionData = await getJSON(`http://localhost:8000/sessions/${session_id}`);
            // Use course_title here
            document.getElementById('sessionDetailsInfo').textContent = `${sessionData.course_title} - ${sessionData.lecturer_name || 'Unknown'}`;
            
            const attendance = await getJSON(`http://localhost:8000/sessions/${session_id}/attendance`);
            const list = document.getElementById('historyAttendanceList');
            list.innerHTML = '';
            attendance.forEach(s => {
                const li = document.createElement('li');
                li.textContent = `${s.matric_no} (${s.name})`;
                list.appendChild(li);
            });
        } catch (err) {
            console.error('Failed to load session details', err);
        }}
    });

    let allSessions = []; // store all sessions globally after loading

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

// Search sessions
document.getElementById('searchBtn').addEventListener('click', () => {
    const query = document.getElementById('sessionSearch').value.toLowerCase();
    const filtered = allSessions.filter(sess =>
        sess.title.toLowerCase().includes(query) ||
        (sess.lecturer_name || '').toLowerCase().includes(query)
    );
    renderSessionList(filtered);
});
