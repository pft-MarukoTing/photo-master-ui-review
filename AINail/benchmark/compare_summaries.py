#!/usr/bin/env python3
"""
compare_summaries.py -- collect summary.txt files from multiple
run_benchmark.py runs (e.g. one per training checkpoint/step) into a single
comparison table, sorted by a chosen metric.

Usage:
    python compare_summaries.py --glob "output_nail_exp_20260814_*"
    python compare_summaries.py --glob "output_nail_exp_20260814_*" --sort-by nail_ssim --csv compare.csv
"""

import argparse
import csv
import glob
import sys
from pathlib import Path

METRIC_KEYS = ["psnr", "ssim", "nail_psnr", "nail_ssim", "background_psnr", "background_ssim"]


def parse_summary(path: Path) -> dict:
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key in METRIC_KEYS:
            data[key] = float(value.split()[0])  # strip trailing " dB" unit
        else:
            data[key] = value
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--glob", required=True,
                         help='shell glob matching each run\'s output dir, e.g. "output_nail_exp_20260814_*"')
    parser.add_argument("--sort-by", default="nail_psnr", choices=METRIC_KEYS,
                         help="metric to sort by (higher is always better for PSNR/SSIM)")
    parser.add_argument("--csv", type=Path, default=None, help="optional path to also write a CSV")
    args = parser.parse_args()

    dirs = sorted(Path(p) for p in glob.glob(args.glob) if Path(p).is_dir())
    rows = []
    for d in dirs:
        summary_path = d / "summary.txt"
        if not summary_path.exists():
            print(f"[warn] {d} has no summary.txt, skipped", file=sys.stderr)
            continue
        row = parse_summary(summary_path)
        row["run_dir"] = d.name
        rows.append(row)

    if not rows:
        print("[error] no summary.txt found for that glob", file=sys.stderr)
        sys.exit(1)

    rows.sort(key=lambda r: r.get(args.sort_by, float("-inf")), reverse=True)

    headers = ["run_dir", "checkpoint", "pairs"] + METRIC_KEYS
    col_width = {h: max(len(h), max(len(f"{r.get(h, '')}") for r in rows)) for h in headers}

    def fmt(value) -> str:
        return f"{value:.4f}" if isinstance(value, float) else str(value)

    def fmt_row(values: dict) -> str:
        return "  ".join(f"{fmt(values.get(h, '')):<{col_width[h]}}" for h in headers)

    print(fmt_row({h: h for h in headers}))
    print("-" * (sum(col_width.values()) + 2 * len(headers)))
    for r in rows:
        print(fmt_row(r))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for r in rows:
                writer.writerow({h: r.get(h, "") for h in headers})
        print(f"[done] csv: {args.csv}")


if __name__ == "__main__":
    main()
