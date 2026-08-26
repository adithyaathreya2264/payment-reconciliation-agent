from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import config

_BUCKET_EDGES = [0.0, 0.45, 0.7, 0.9, 1.0]


def _load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_calibration_table(matches: list[dict], gt_entries: list[dict]) -> list[dict]:
    entries_by_bank = {e["bank_record_id"]: e for e in gt_entries if e["bank_record_id"]}
    rows = []
    for m in matches:
        e = entries_by_bank.get(m["bank_record_id"])
        if e is None:
            continue  
        is_correct = set(m["matched_invoice_ids"]) == set(e["true_match_ids"])
        rows.append({
            "bank_record_id": m["bank_record_id"],
            "tier": m["tier"],
            "confidence": m["confidence"],
            "is_correct": is_correct,
        })
    return rows


def reliability_table(rows: list[dict], bucket_edges: list[float] = _BUCKET_EDGES, tier: str | None = None) -> list[dict]:
    if tier is not None:
        rows = [r for r in rows if r["tier"] == tier]

    buckets = []
    for i in range(len(bucket_edges) - 1):
        lo, hi = bucket_edges[i], bucket_edges[i + 1]
        is_last = i == len(bucket_edges) - 2
        in_bucket = [r for r in rows if (lo <= r["confidence"] < hi) or (is_last and r["confidence"] == hi)]
        buckets.append(_summarize_bucket(lo, hi, in_bucket))

    
    exact_one = [r for r in rows if r["confidence"] == 1.0]
    if bucket_edges[-1] != 1.0 or len(bucket_edges) < 2:
        buckets.append(_summarize_bucket(1.0, 1.0, exact_one))
    return buckets


def _summarize_bucket(lo: float, hi: float, rows: list[dict]) -> dict:
    n = len(rows)
    n_correct = sum(1 for r in rows if r["is_correct"])
    accuracy = round(n_correct / n, 3) if n else None
    mean_confidence = round(sum(r["confidence"] for r in rows) / n, 3) if n else None
    gap = round(mean_confidence - accuracy, 3) if (accuracy is not None and mean_confidence is not None) else None
    return {
        "bucket": f"[{lo}, {hi}{']' if hi == 1.0 else ')'}",
        "n": n,
        "n_correct": n_correct,
        "empirical_accuracy": accuracy,
        "mean_confidence": mean_confidence,
        "calibration_gap": gap,
    }


def threshold_scan(rows: list[dict]) -> list[dict]:
    
    distinct = sorted({r["confidence"] for r in rows})
    scan = []
    for cutoff in distinct:
        above = [r for r in rows if r["confidence"] >= cutoff]
        below = [r for r in rows if r["confidence"] < cutoff]
        scan.append({
            "cutoff": cutoff,
            "n_at_or_above": len(above),
            "accuracy_at_or_above": round(sum(1 for r in above if r["is_correct"]) / len(above), 3) if above else None,
            "n_below": len(below),
            "accuracy_below": round(sum(1 for r in below if r["is_correct"]) / len(below), 3) if below else None,
        })
    return scan


def classify_band(confidence: float) -> str:
    if confidence >= config.AUTO_MATCH_MIN_CONFIDENCE:
        return "auto_match"
    if confidence >= config.REVIEW_QUEUE_MIN_CONFIDENCE:
        return "needs_review"
    return "exception"


def band_summary(rows: list[dict]) -> dict:
    bands: dict[str, list[dict]] = {"auto_match": [], "needs_review": [], "exception": []}
    for r in rows:
        bands[classify_band(r["confidence"])].append(r)
    return {
        band: {
            "n": len(rs),
            "n_correct": sum(1 for r in rs if r["is_correct"]),
            "empirical_accuracy": round(sum(1 for r in rs if r["is_correct"]) / len(rs), 3) if rs else None,
        }
        for band, rs in bands.items()
    }


def build_report(matches: list[dict], gt_entries: list[dict]) -> dict:
    rows = build_calibration_table(matches, gt_entries)
    return {
        "n_total": len(rows),
        "overall": reliability_table(rows),
        "by_tier": {
            tier: reliability_table(rows, tier=tier)
            for tier in ("exact", "tolerance", "subset_sum")
        },
        "threshold_scan": threshold_scan(rows),
        "bands": band_summary(rows),
    }


def print_report(report: dict) -> None:
    def print_table(title: str, table: list[dict]) -> None:
        print(f"\n=== {title} ===")
        for b in table:
            print(f"  {b['bucket']:<12} n={b['n']:>4}  correct={b['n_correct']:>4}  "
                  f"accuracy={b['empirical_accuracy']}  mean_confidence={b['mean_confidence']}  "
                  f"gap={b['calibration_gap']}")

    print(f"Total resolved matches with ground truth: {report['n_total']}")
    print_table("Overall reliability", report["overall"])
    for tier, table in report["by_tier"].items():
        print_table(f"Tier: {tier}", table)

    print("\n=== Threshold scan (empirical accuracy at/above vs below each observed confidence value) ===")
    for s in report["threshold_scan"]:
        print(f"  cutoff={s['cutoff']:<6} at/above: n={s['n_at_or_above']:>4} acc={s['accuracy_at_or_above']}   "
              f"below: n={s['n_below']:>4} acc={s['accuracy_below']}")

    print("\n=== Auto-match / review / exception bands (config.py thresholds) ===")
    for band, stats in report["bands"].items():
        print(f"  {band:<14} n={stats['n']:>4}  correct={stats['n_correct']:>4}  accuracy={stats['empirical_accuracy']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check matcher confidence calibration against ground truth.")
    parser.add_argument("--matcher-output", type=Path, default=Path("matcher_output"))
    parser.add_argument("--ground-truth", type=Path, default=Path("data/answer_key/ground_truth.json"))
    args = parser.parse_args()

    matches = _load_json(args.matcher_output / "matches.json")
    gt = _load_json(args.ground_truth)
    report = build_report(matches, gt["entries"])

    print_report(report)
    with open(args.matcher_output / "calibration_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    main()
