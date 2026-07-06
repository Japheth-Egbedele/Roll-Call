# Roll-Call Face Verification — Research Data Pack

**Generated:** 2026-07-02 18:39 UTC
**System:** Roll-Call hybrid attendance (FaceID + QR), Python FastAPI backend
**Purpose:** Single source of truth for drafting the research paper. Paste this entire document into Claude.

---

## 1. Study design

- **Participants (N):** 20 volunteers (P01–P20)
- **Enrollment:** 1 frontal gallery image per participant (`dataset/gallery/Pxx_enroll.jpg`)
- **Verification probes:** 5 pose variants per participant (center, left, right, close, far) → 100 genuine probe images total
- **Protocol tool:** `research/quick_volunteer.py` (webcam capture → live API enroll → live API verify session)
- **Data collection date:** 2026-07-02
- **Environment:** Windows 10, local FastAPI server, MongoDB Atlas (`verification_attempts` collection)

### Trial types

| Trial type | Count | Description |
|------------|-------|-------------|
| Genuine (1:N) | 100 | Probe from person A matched against full gallery; correct if top match is A |
| Impostor (1:1) | 1900 | Probe from person A tested against each other gallery member B (A≠B); false accept if distance ≤ τ |
| **Total offline** | **2000** | Used for FAR/FRR threshold analysis |
| Live API attempts | 121 | Real HTTP requests with server-side `processing_time_ms` |

---

## 2. Face recognition pipeline (matches production)

| Parameter | Value |
|-----------|-------|
| Library | `face_recognition` 1.2.3 (dlib) |
| Face detector | HOG (default) |
| Embedding | 128-dimensional face encoding |
| Distance metric | Euclidean (`face_recognition.face_distance`) |
| **Production threshold τ** | **0.4** (`FACE_MATCH_TOLERANCE`) |
| Match rule | Accept if min distance ≤ τ |
| Identification | 1:N (probe vs all gallery encodings, argmin distance) |

---

## 3. Primary results at production threshold (τ = 0.40)

### 3a. Offline verification (recommended for FAR/FRR in paper)

Source: `threshold_sweep.json` (2000-trial offline benchmark). Pose/participant breakdown from `Pxx_attempts.csv`.

| Metric | Value |
|--------|-------|
| FAR (False Accept Rate) | **4.74%** (90 FP / 1900 impostor trials) |
| FRR (False Reject Rate) | **21.00%** (21 FN / 100 genuine trials) |
| Accuracy | **94.45%** |
| Precision | N/A (imbalanced trial mix; use FAR/FRR) |
| Genuine accept rate (TPR) | 79.00% |
| TP / TN / FP / FN | 79 / 1810 / 90 / 21 |

### 3b. Live API session performance

Source: `research/output/live_attempts.csv` (MongoDB export)

**Note:** Live logs contain only genuine volunteer sessions (no synthetic impostor attacks). Use offline benchmark for FAR.

| Endpoint | Latency (server `processing_time_ms`) |
|----------|---------------------------------------|
| Enrollment (success only, n=20) | n=20, mean=1715 ms, median=1728 ms, min=791 ms, max=2339 ms, σ=277 ms |
| Verify — all requests | n=100, mean=2359 ms, median=2267 ms, min=853 ms, max=5897 ms, σ=688 ms |
| Verify — confirmed match | n=82, mean=2319 ms, median=2262 ms, min=853 ms, max=5465 ms, σ=625 ms |
| Verify — rejected (pose/no-match) | n=18, mean=2538 ms, median=2324 ms, min=1744 ms, max=5897 ms, σ=925 ms |

- Failed enrollments (no face detected): **1** (excluded from enroll latency)
- Live verify rejections during collection: **18** (difficult poses retried until confirmed; expected during multi-pose protocol)

---

## 4. Threshold sweep (FAR vs FRR trade-off)

Source: `research/output/threshold_sweep.json`

| τ | FAR | FRR | Accuracy | TP | TN | FP | FN |
|---|-----|-----|----------|----|----|----|-----|
| 0.35 | 0.21% | 34.00% | 98.10% | 66 | 1896 | 4 | 34 |
| 0.40 | 4.74% | 21.00% | 94.45% | 79 | 1810 | 90 | 21 | **← production**
| 0.45 | 18.37% | 14.00% | 81.85% | 86 | 1551 | 349 | 14 |
| 0.50 | 39.16% | 11.00% | 62.25% | 89 | 1156 | 744 | 11 |

**Interpretation for paper:**
- At τ=0.4, the system achieves **21.00% FRR** and **4.74% FAR** on N=20.
- Lower τ reduces impostor accepts (security↑) but increases genuine rejects (usability↓).
- τ=0.35 yields FAR≈0.21% but FRR=34% — too strict for classroom use.
- τ=0.45+ trades security for usability (FAR>18%).

---

## 5. Genuine verification by pose (offline, τ = 0.40)

Source: per-participant `Pxx_attempts.csv` offline genuine slices (same pipeline as full benchmark)

| Pose | Correct (TP) | Failed (FN) | Accept rate |
|------|--------------|-------------|-------------|
| center | 20 | 0 | 100.00% |
| close | 19 | 1 | 95.00% |
| far | 20 | 0 | 100.00% |
| left | 10 | 10 | 50.00% |
| right | 10 | 10 | 50.00% |

---

## 6. Per-participant genuine accept rate (offline, τ = 0.40)

| Participant | TP | FN | Accept rate |
|-------------|----|----|-------------|
| P01 | 5 | 0 | 100.00% |
| P02 | 4 | 1 | 80.00% |
| P03 | 3 | 2 | 60.00% |
| P04 | 4 | 1 | 80.00% |
| P05 | 5 | 0 | 100.00% |
| P06 | 3 | 2 | 60.00% |
| P07 | 3 | 2 | 60.00% |
| P08 | 3 | 2 | 60.00% |
| P09 | 5 | 0 | 100.00% |
| P10 | 5 | 0 | 100.00% |
| P11 | 2 | 3 | 40.00% |
| P12 | 5 | 0 | 100.00% |
| P13 | 3 | 2 | 60.00% |
| P14 | 5 | 0 | 100.00% |
| P15 | 3 | 2 | 60.00% |
| P16 | 5 | 0 | 100.00% |
| P17 | 4 | 1 | 80.00% |
| P18 | 3 | 2 | 60.00% |
| P19 | 4 | 1 | 80.00% |
| P20 | 5 | 0 | 100.00% |

---

## 7. Genuine match distance statistics (offline)

When genuine probe matches correctly, Euclidean distance to enrolled self: **n=79, mean=0.214, median=0.261, min=0.000, max=0.399**
(Lower distance = higher similarity; threshold τ=0.40)

---

## 8. Suggested paper wording (copy/adapt)

### Methods excerpt
> We evaluated the face verification module with 20 participants. Each participant contributed one enrollment image and five verification probes varying head pose and distance. Genuine trials used 1:N identification against the full gallery. Impostor trials paired each probe with every other participant's enrollment (1900 impostor comparisons). Metrics were computed at Euclidean distance thresholds τ ∈ {0.35, 0.4, 0.45, 0.5} using the face_recognition library (128-d HOG embeddings). Live end-to-end latency was measured from server-side processing time on real API requests.

### Results excerpt (production τ)
> At the deployed threshold τ=0.4, offline evaluation yielded FAR=4.74% and FRR=21.00% (accuracy 94.45%). Mean server processing time was 1715 ms for enrollment and 2359 ms for verification (N=20 participants).

### Limitations (disclose in paper)
- Volunteer sample (university lab), not adversarial makeup/print attacks.
- Impostor trials are **zero-effort** (cross-person), not presentation attacks.
- Multi-pose protocol caused some live rejections before retry; offline FRR is the cleaner metric.
- Single enrollment image per person; no illumination-controlled capture booth.
- Windows laptop webcams; results may vary on mobile or GPU-accelerated pipelines.

---

## 9. Raw data file index

| File | Contents |
|------|----------|
| `dataset/gallery/` | 20 enrollment images |
| `dataset/probes/genuine/` | 100 verification probe images |
| `research/output/live_attempts.csv` | 121 live API metric rows |
| `research/output/full_benchmark.csv` | Optional ~2000 offline trial rows (`python research/run_full_benchmark.py`) |
| `research/output/threshold_sweep.json` | FAR/FRR at 4 thresholds |
| `research/output/P01_attempts.csv` … `P20_attempts.csv` | Per-participant offline genuine slices |
| `research/output/PAPER_DATA_PACK.md` | **This document** |
| `research/generate_paper_pack.py` | Regenerate: `python research/generate_paper_pack.py --skip-benchmark` |

---

## 10. Machine-readable summary (JSON)

```json
{
  "participants": 20,
  "production_tau": 0.4,
  "offline_at_production_tau": {
    "tau": 0.4,
    "FAR": 0.04736842105263158,
    "FRR": 0.21,
    "accuracy": 0.9445,
    "TP": 79,
    "TN": 1810,
    "FP": 90,
    "FN": 21,
    "n_attempts": 2000,
    "gallery_size": 20
  },
  "threshold_sweep": [
    {
      "tau": 0.35,
      "FAR": 0.002105263157894737,
      "FRR": 0.34,
      "accuracy": 0.981,
      "TP": 66,
      "TN": 1896,
      "FP": 4,
      "FN": 34,
      "n_attempts": 2000,
      "gallery_size": 20
    },
    {
      "tau": 0.4,
      "FAR": 0.04736842105263158,
      "FRR": 0.21,
      "accuracy": 0.9445,
      "TP": 79,
      "TN": 1810,
      "FP": 90,
      "FN": 21,
      "n_attempts": 2000,
      "gallery_size": 20
    },
    {
      "tau": 0.45,
      "FAR": 0.18368421052631578,
      "FRR": 0.14,
      "accuracy": 0.8185,
      "TP": 86,
      "TN": 1551,
      "FP": 349,
      "FN": 14,
      "n_attempts": 2000,
      "gallery_size": 20
    },
    {
      "tau": 0.5,
      "FAR": 0.391578947368421,
      "FRR": 0.11,
      "accuracy": 0.6225,
      "TP": 89,
      "TN": 1156,
      "FP": 744,
      "FN": 11,
      "n_attempts": 2000,
      "gallery_size": 20
    }
  ],
  "live_latency_ms": {
    "enroll_success": {
      "n": 20,
      "mean": 1715.1970800070558,
      "median": 1728.1161999562755,
      "min": 791.1234999774024,
      "max": 2338.713299948722,
      "stdev": 277.31549379247633
    },
    "verify_all": {
      "n": 100,
      "mean": 2358.7726219941396,
      "median": 2266.7158000404015,
      "min": 852.8857999481261,
      "max": 5897.4922999041155,
      "stdev": 688.2962435102688
    }
  },
  "dataset": {
    "gallery_images": 20,
    "genuine_probe_images": 100
  },
  "generated_at": "2026-07-02 18:39 UTC"
}
```
