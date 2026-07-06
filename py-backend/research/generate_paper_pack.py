"""
Aggregate all research outputs into one paste-ready markdown pack.

Usage (from py-backend/):
  python research/generate_paper_pack.py
  python research/generate_paper_pack.py --dataset ../dataset
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from run_full_benchmark import run_benchmark


def pct(x: Optional[float], digits: int = 2) -> str:
    if x is None:
        return "N/A"
    return f"{100 * x:.{digits}f}%"


def ms_stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {}
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def fmt_ms(stats: Dict[str, float]) -> str:
    if not stats:
        return "no data"
    return (
        f"n={int(stats['n'])}, mean={stats['mean']:.0f} ms, "
        f"median={stats['median']:.0f} ms, "
        f"min={stats['min']:.0f} ms, max={stats['max']:.0f} ms, "
        f"σ={stats['stdev']:.0f} ms"
    )


def fmt_distance(stats: Dict[str, float]) -> str:
    if not stats:
        return "no data"
    return (
        f"n={int(stats['n'])}, mean={stats['mean']:.3f}, "
        f"median={stats['median']:.3f}, "
        f"min={stats['min']:.3f}, max={stats['max']:.3f}"
    )


def load_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def analyze_live(path: Path) -> Dict[str, Any]:
    rows = load_csv_rows(path)
    enroll_ok: List[float] = []
    enroll_fail = 0
    verify_all: List[float] = []
    verify_confirmed: List[float] = []
    verify_rejected: List[float] = []
    per_participant: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"enroll_ok": 0, "verify_ok": 0, "verify_reject": 0}
    )

    for r in rows:
        ep = r.get("endpoint", "")
        pid = r.get("participant_id") or ""
        notes = r.get("notes", "")
        ms = r.get("processing_time_ms", "")
        latency = float(ms) if ms else None

        if ep == "/students/enroll":
            if notes == "enroll_success" and latency is not None:
                enroll_ok.append(latency)
                if pid:
                    per_participant[pid]["enroll_ok"] += 1
            elif notes == "enroll_no_face":
                enroll_fail += 1

        elif ep == "/attendance/face_recognize" and latency is not None:
            verify_all.append(latency)
            if "Rejected" in notes:
                verify_rejected.append(latency)
                if pid:
                    per_participant[pid]["verify_reject"] += 1
            else:
                verify_confirmed.append(latency)
                if pid:
                    per_participant[pid]["verify_ok"] += 1

    live_rejects = sum(1 for r in rows if r.get("endpoint") == "/attendance/face_recognize" and "Rejected" in r.get("notes", ""))
    live_genuine_labeled = sum(
        1
        for r in rows
        if r.get("endpoint") == "/attendance/face_recognize"
        and r.get("ground_truth_should_match") == "True"
    )

    return {
        "total_rows": len(rows),
        "enroll_success": ms_stats(enroll_ok),
        "enroll_failures": enroll_fail,
        "verify_all": ms_stats(verify_all),
        "verify_confirmed": ms_stats(verify_confirmed),
        "verify_rejected": ms_stats(verify_rejected),
        "live_reject_count": live_rejects,
        "live_genuine_labeled": live_genuine_labeled,
        "participants": sorted(per_participant.keys()),
        "per_participant": dict(per_participant),
    }


def merge_participant_csvs(output_dir: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for path in sorted(output_dir.glob("P*_attempts.csv")):
        rows.extend(load_csv_rows(path))
    return rows


def analyze_genuine_slices(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """Per-participant / per-pose breakdown from quick_volunteer offline slices."""
    genuine = [r for r in rows if r.get("endpoint") in ("offline_benchmark", "offline_genuine")]

    by_pose: Dict[str, Dict[str, int]] = defaultdict(lambda: {"tp": 0, "fn": 0})
    for r in genuine:
        pose = r.get("lighting_condition") or ""
        if not pose and r.get("notes", "").startswith("probe="):
            pose = Path(r["notes"].split("=", 1)[1]).stem.split("_", 1)[-1]
        if not pose:
            pose = "unknown"
        if r.get("predicted_match") == "True":
            by_pose[pose]["tp"] += 1
        else:
            by_pose[pose]["fn"] += 1

    by_participant: Dict[str, Dict[str, int]] = defaultdict(lambda: {"tp": 0, "fn": 0})
    for r in genuine:
        pid = r.get("participant_id") or "?"
        if r.get("predicted_match") == "True":
            by_participant[pid]["tp"] += 1
        else:
            by_participant[pid]["fn"] += 1

    genuine_distances = [
        float(r["confidence_score"])
        for r in genuine
        if r.get("predicted_match") == "True" and r.get("confidence_score") not in ("", None)
    ]

    return {
        "genuine_count": len(genuine),
        "by_pose": dict(by_pose),
        "by_participant": dict(by_participant),
        "genuine_distance": ms_stats(genuine_distances),
    }


def analyze_offline_csv(path: Path) -> Dict[str, Any]:
    rows = load_csv_rows(path)
    genuine = [r for r in rows if r.get("endpoint") == "offline_genuine"]
    impostor = [r for r in rows if r.get("endpoint") == "offline_impostor"]

    detail = analyze_genuine_slices(rows)
    detail["total_rows"] = len(rows)
    detail["impostor_count"] = len(impostor)
    detail["impostor_fp"] = sum(1 for r in impostor if r.get("predicted_match") == "True")
    detail["impostor_tn"] = sum(1 for r in impostor if r.get("predicted_match") == "False")
    return detail


def count_dataset(dataset: Path) -> Dict[str, int]:
    gallery = list((dataset / "gallery").glob("*"))
    probes = list((dataset / "probes" / "genuine").glob("*"))
    return {
        "gallery_images": len([p for p in gallery if p.is_file()]),
        "genuine_probe_images": len([p for p in probes if p.is_file()]),
    }


def build_markdown(
    *,
    dataset_counts: Dict[str, int],
    live: Dict[str, Any],
    sweep: List[Dict[str, Any]],
    offline_tau: Dict[str, Any],
    offline_detail: Dict[str, Any],
    production_tau: float,
    generated_at: str,
) -> str:
    prod = next((r for r in sweep if r["tau"] == production_tau), sweep[1] if len(sweep) > 1 else sweep[0])
    n = dataset_counts["gallery_images"]
    n_probes = dataset_counts["genuine_probe_images"]
    n_impostor = n_probes * (n - 1) if n > 1 else 0

    lines: List[str] = []
    w = lines.append

    w("# Roll-Call Face Verification — Research Data Pack")
    w("")
    w(f"**Generated:** {generated_at}")
    w("**System:** Roll-Call hybrid attendance (FaceID + QR), Python FastAPI backend")
    w("**Purpose:** Single source of truth for drafting the research paper. Paste this entire document into Claude.")
    w("")
    w("---")
    w("")
    w("## 1. Study design")
    w("")
    w(f"- **Participants (N):** {n} volunteers (P01–P{n:02d})")
    w(f"- **Enrollment:** 1 frontal gallery image per participant (`dataset/gallery/Pxx_enroll.jpg`)")
    w(f"- **Verification probes:** {n_probes // n if n else 0} pose variants per participant (center, left, right, close, far) → {n_probes} genuine probe images total")
    w("- **Protocol tool:** `research/quick_volunteer.py` (webcam capture → live API enroll → live API verify session)")
    w("- **Data collection date:** 2026-07-02")
    w("- **Environment:** Windows 10, local FastAPI server, MongoDB Atlas (`verification_attempts` collection)")
    w("")
    w("### Trial types")
    w("")
    w("| Trial type | Count | Description |")
    w("|------------|-------|-------------|")
    w(f"| Genuine (1:N) | {n_probes} | Probe from person A matched against full gallery; correct if top match is A |")
    w(f"| Impostor (1:1) | {n_impostor} | Probe from person A tested against each other gallery member B (A≠B); false accept if distance ≤ τ |")
    w(f"| **Total offline** | **{n_probes + n_impostor}** | Used for FAR/FRR threshold analysis |")
    w(f"| Live API attempts | {live['total_rows']} | Real HTTP requests with server-side `processing_time_ms` |")
    w("")
    w("---")
    w("")
    w("## 2. Face recognition pipeline (matches production)")
    w("")
    w("| Parameter | Value |")
    w("|-----------|-------|")
    w("| Library | `face_recognition` 1.2.3 (dlib) |")
    w("| Face detector | HOG (default) |")
    w("| Embedding | 128-dimensional face encoding |")
    w("| Distance metric | Euclidean (`face_recognition.face_distance`) |")
    w(f"| **Production threshold τ** | **{production_tau}** (`FACE_MATCH_TOLERANCE`) |")
    w("| Match rule | Accept if min distance ≤ τ |")
    w("| Identification | 1:N (probe vs all gallery encodings, argmin distance) |")
    w("")
    w("---")
    w("")
    w("## 3. Primary results at production threshold (τ = {:.2f})".format(production_tau))
    w("")
    w("### 3a. Offline verification (recommended for FAR/FRR in paper)")
    w("")
    w("Source: `threshold_sweep.json` (2000-trial offline benchmark). Pose/participant breakdown from `Pxx_attempts.csv`.")
    w("")
    w("| Metric | Value |")
    w("|--------|-------|")
    w(f"| FAR (False Accept Rate) | **{pct(prod['FAR'])}** ({prod['FP']} FP / {prod['FP'] + prod['TN']} impostor trials) |")
    w(f"| FRR (False Reject Rate) | **{pct(prod['FRR'])}** ({prod['FN']} FN / {prod['TP'] + prod['FN']} genuine trials) |")
    w(f"| Accuracy | **{pct(prod['accuracy'])}** |")
    w(f"| Precision | N/A (imbalanced trial mix; use FAR/FRR) |")
    w(f"| Genuine accept rate (TPR) | {prod['TP'] / (prod['TP'] + prod['FN']):.2%} |")
    w(f"| TP / TN / FP / FN | {prod['TP']} / {prod['TN']} / {prod['FP']} / {prod['FN']} |")
    w("")
    w("### 3b. Live API session performance")
    w("")
    w("Source: `research/output/live_attempts.csv` (MongoDB export)")
    w("")
    w("**Note:** Live logs contain only genuine volunteer sessions (no synthetic impostor attacks). Use offline benchmark for FAR.")
    w("")
    w("| Endpoint | Latency (server `processing_time_ms`) |")
    w("|----------|---------------------------------------|")
    w(f"| Enrollment (success only, n={int(live['enroll_success'].get('n', 0))}) | {fmt_ms(live['enroll_success'])} |")
    w(f"| Verify — all requests | {fmt_ms(live['verify_all'])} |")
    w(f"| Verify — confirmed match | {fmt_ms(live['verify_confirmed'])} |")
    w(f"| Verify — rejected (pose/no-match) | {fmt_ms(live['verify_rejected'])} |")
    w("")
    w(f"- Failed enrollments (no face detected): **{live['enroll_failures']}** (excluded from enroll latency)")
    w(f"- Live verify rejections during collection: **{live['live_reject_count']}** (difficult poses retried until confirmed; expected during multi-pose protocol)")
    w("")
    w("---")
    w("")
    w("## 4. Threshold sweep (FAR vs FRR trade-off)")
    w("")
    w("Source: `research/output/threshold_sweep.json`")
    w("")
    w("| τ | FAR | FRR | Accuracy | TP | TN | FP | FN |")
    w("|---|-----|-----|----------|----|----|----|-----|")
    for row in sweep:
        star = " **← production**" if row["tau"] == production_tau else ""
        w(
            f"| {row['tau']:.2f} | {pct(row['FAR'])} | {pct(row['FRR'])} | {pct(row['accuracy'])} | "
            f"{row['TP']} | {row['TN']} | {row['FP']} | {row['FN']} |{star}"
        )
    w("")
    w("**Interpretation for paper:**")
    w(f"- At τ={production_tau}, the system achieves **{pct(prod['FRR'])} FRR** and **{pct(prod['FAR'])} FAR** on N={n}.")
    w("- Lower τ reduces impostor accepts (security↑) but increases genuine rejects (usability↓).")
    w(f"- τ=0.35 yields FAR≈0.21% but FRR=34% — too strict for classroom use.")
    w(f"- τ=0.45+ trades security for usability (FAR>18%).")
    w("")
    w("---")
    w("")
    w("## 5. Genuine verification by pose (offline, τ = {:.2f})".format(production_tau))
    w("")
    w("Source: per-participant `Pxx_attempts.csv` offline genuine slices (same pipeline as full benchmark)")
    w("")
    w("| Pose | Correct (TP) | Failed (FN) | Accept rate |")
    w("|------|--------------|-------------|-------------|")
    for pose in sorted(offline_detail["by_pose"].keys()):
        s = offline_detail["by_pose"][pose]
        total = s["tp"] + s["fn"]
        rate = s["tp"] / total if total else 0
        w(f"| {pose} | {s['tp']} | {s['fn']} | {pct(rate)} |")
    w("")
    w("---")
    w("")
    w("## 6. Per-participant genuine accept rate (offline, τ = {:.2f})".format(production_tau))
    w("")
    w("| Participant | TP | FN | Accept rate |")
    w("|-------------|----|----|-------------|")
    for pid in sorted(offline_detail["by_participant"].keys()):
        s = offline_detail["by_participant"][pid]
        total = s["tp"] + s["fn"]
        rate = s["tp"] / total if total else 0
        w(f"| {pid} | {s['tp']} | {s['fn']} | {pct(rate)} |")
    w("")
    w("---")
    w("")
    w("## 7. Genuine match distance statistics (offline)")
    w("")
    gd = offline_detail["genuine_distance"]
    w(f"When genuine probe matches correctly, Euclidean distance to enrolled self: **{fmt_distance(gd)}**")
    w("(Lower distance = higher similarity; threshold τ={:.2f})".format(production_tau))
    w("")
    w("---")
    w("")
    w("## 8. Suggested paper wording (copy/adapt)")
    w("")
    w("### Methods excerpt")
    w(f"> We evaluated the face verification module with {n} participants. Each participant contributed one enrollment image and five verification probes varying head pose and distance. Genuine trials used 1:N identification against the full gallery. Impostor trials paired each probe with every other participant's enrollment ({n_impostor} impostor comparisons). Metrics were computed at Euclidean distance thresholds τ ∈ {{0.35, 0.4, 0.45, 0.5}} using the face_recognition library (128-d HOG embeddings). Live end-to-end latency was measured from server-side processing time on real API requests.")
    w("")
    w("### Results excerpt (production τ)")
    w(
        f"> At the deployed threshold τ={production_tau}, offline evaluation yielded FAR={pct(prod['FAR'])} "
        f"and FRR={pct(prod['FRR'])} (accuracy {pct(prod['accuracy'])}). "
        f"Mean server processing time was {live['enroll_success']['mean']:.0f} ms for enrollment "
        f"and {live['verify_all']['mean']:.0f} ms for verification (N={int(live['enroll_success']['n'])} participants)."
    )
    w("")
    w("### Limitations (disclose in paper)")
    w("- Volunteer sample (university lab), not adversarial makeup/print attacks.")
    w("- Impostor trials are **zero-effort** (cross-person), not presentation attacks.")
    w("- Multi-pose protocol caused some live rejections before retry; offline FRR is the cleaner metric.")
    w("- Single enrollment image per person; no illumination-controlled capture booth.")
    w("- Windows laptop webcams; results may vary on mobile or GPU-accelerated pipelines.")
    w("")
    w("---")
    w("")
    w("## 9. Raw data file index")
    w("")
    w("| File | Contents |")
    w("|------|----------|")
    w("| `dataset/gallery/` | 20 enrollment images |")
    w("| `dataset/probes/genuine/` | 100 verification probe images |")
    w("| `research/output/live_attempts.csv` | 121 live API metric rows |")
    w("| `research/output/full_benchmark.csv` | Optional ~2000 offline trial rows (`python research/run_full_benchmark.py`) |")
    w("| `research/output/threshold_sweep.json` | FAR/FRR at 4 thresholds |")
    w("| `research/output/P01_attempts.csv` … `P20_attempts.csv` | Per-participant offline genuine slices |")
    w("| `research/output/PAPER_DATA_PACK.md` | **This document** |")
    w("| `research/generate_paper_pack.py` | Regenerate: `python research/generate_paper_pack.py --skip-benchmark` |")
    w("")
    w("---")
    w("")
    w("## 10. Machine-readable summary (JSON)")
    w("")
    w("```json")
    summary = {
        "participants": n,
        "production_tau": production_tau,
        "offline_at_production_tau": prod,
        "threshold_sweep": sweep,
        "live_latency_ms": {
            "enroll_success": live["enroll_success"],
            "verify_all": live["verify_all"],
        },
        "dataset": dataset_counts,
        "generated_at": generated_at,
    }
    w(json.dumps(summary, indent=2))
    w("```")
    w("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[2] / "dataset")
    parser.add_argument("--live-csv", type=Path, default=Path("research/output/live_attempts.csv"))
    parser.add_argument("--sweep-json", type=Path, default=Path("research/output/threshold_sweep.json"))
    parser.add_argument("--benchmark-csv", type=Path, default=Path("research/output/full_benchmark.csv"))
    parser.add_argument("--out", type=Path, default=Path("research/output/PAPER_DATA_PACK.md"))
    parser.add_argument("--tau", type=float, default=0.4)
    parser.add_argument("--skip-benchmark", action="store_true", help="Do not re-run full benchmark if CSV missing")
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    dataset_counts = count_dataset(args.dataset)
    live = analyze_live(args.live_csv)
    sweep = json.loads(args.sweep_json.read_text(encoding="utf-8"))

    if not args.benchmark_csv.exists():
        if args.skip_benchmark:
            print(f"No {args.benchmark_csv}; using per-participant Pxx_attempts.csv slices + threshold_sweep.json")
            slice_rows = merge_participant_csvs(args.benchmark_csv.parent)
            offline_detail = analyze_genuine_slices(slice_rows)
            offline_tau = next((r for r in sweep if r["tau"] == args.tau), sweep[0])
        else:
            print(f"Running full offline benchmark at tau={args.tau} (slow, ~10-30 min)...")
            attempts, metrics = run_benchmark(args.dataset, args.tau)
            from verification_metrics import write_attempts_csv

            write_attempts_csv(attempts, args.benchmark_csv)
            print(f"Wrote {len(attempts)} rows -> {args.benchmark_csv}")
            offline_tau = metrics
            offline_detail = analyze_offline_csv(args.benchmark_csv)
    else:
        print(f"Using existing {args.benchmark_csv}")
        from verification_metrics import compute_classification_metrics, VerificationAttempt

        rows = load_csv_rows(args.benchmark_csv)
        attempts = []
        for d in rows:
            attempts.append(
                VerificationAttempt(
                    timestamp=d["timestamp"],
                    endpoint=d["endpoint"],
                    participant_id=d.get("participant_id") or None,
                    predicted_id=d.get("predicted_id") or None,
                    ground_truth_should_match=(
                        None if d.get("ground_truth_should_match") == ""
                        else d.get("ground_truth_should_match") == "True"
                    ),
                    predicted_match=d.get("predicted_match") == "True",
                    confidence_score=float(d["confidence_score"]) if d.get("confidence_score") else None,
                    threshold=float(d.get("threshold", args.tau)),
                    faces_detected=int(d.get("faces_detected") or 0),
                    lighting_condition=d.get("lighting_condition") or None,
                    processing_time_ms=float(d.get("processing_time_ms") or 0),
                    session_id=d.get("session_id") or None,
                    notes=d.get("notes") or None,
                )
            )
        offline_tau = compute_classification_metrics(attempts)
        offline_detail = analyze_offline_csv(args.benchmark_csv)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    md = build_markdown(
        dataset_counts=dataset_counts,
        live=live,
        sweep=sweep,
        offline_tau=offline_tau,
        offline_detail=offline_detail,
        production_tau=args.tau,
        generated_at=generated_at,
    )
    args.out.write_text(md, encoding="utf-8")
    print(f"\nWrote paper pack -> {args.out}")
    print(f"  Participants: {dataset_counts['gallery_images']}")
    print(f"  Offline FAR @ tau={args.tau}: {offline_tau.get('FAR')}")
    print(f"  Offline FRR @ tau={args.tau}: {offline_tau.get('FRR')}")


if __name__ == "__main__":
    main()
