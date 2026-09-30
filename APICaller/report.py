#!/usr/bin/env python3
"""report.py -- render pipeline_log.jsonl records into a static HTML gallery
(source / reference / result thumbnails + judge verdict per run).

Usage:
    conda activate apicaller
    python report.py                      # all records in pipeline_log.jsonl -> report.html
    python report.py --last 3             # only the most recent 3 runs
    python report.py --log other.jsonl --out other_report.html
"""

import argparse
import base64
import json
import mimetypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_LOG_PATH = HERE / "pipeline_log.jsonl"
DEFAULT_OUT_PATH = HERE / "report.html"


def load_records(log_path: Path) -> list:
    records = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _image_data_uri(path_str: str) -> str:
    path = Path(path_str)
    if not path.exists():
        return ""
    content_type = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:{content_type};base64,{b64}"


def render_report(records: list, out_path: Path) -> Path:
    cards = []
    grid = []
    for i, record in enumerate(records, 1):
        judge = record.get("judge") or {}
        verdict = judge.get("verdict", "unknown")
        reason = judge.get("reason", "")
        badge_class = "good" if verdict == "good" else "bad"

        images = []
        row_has_image = []
        for col, (label, key) in enumerate((("Source", "source"), ("Reference", "reference"), ("Result", "result"))):
            uri = _image_data_uri(record.get(key, ""))
            caption = f"#{i} -- {label}"
            if uri:
                images.append(
                    f'<figure><img src="{uri}" loading="lazy" data-row="{i - 1}" data-col="{col}" '
                    f'data-caption="{caption}" onclick="openAt({i - 1},{col})">'
                    f'<figcaption>{label}</figcaption></figure>'
                )
                row_has_image.append(True)
            else:
                images.append(f'<figure class="missing"><div class="placeholder">missing</div><figcaption>{label}</figcaption></figure>')
                row_has_image.append(False)
        grid.append(row_has_image)

        cards.append(f"""
        <section class="card">
          <header>
            <span class="run-id">#{i} -- {record.get("run_id", "")}</span>
            <span class="badge {badge_class}">{verdict}</span>
          </header>
          <div class="meta">
            <span>generate: <code>{record.get("gen_model", "")}</code></span>
            <span>judge: <code>{record.get("judge_model", "")}</code></span>
            <span>checkpoint: <code>{record.get("checkpoint", "")}</code></span>
          </div>
          <div class="images">{"".join(images)}</div>
          <p class="reason">{reason}</p>
        </section>""")

    html = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nail-Transfer Pipeline Report</title>
<style>
  :root {{
    --bg: #f6f3ef;
    --surface: #ffffff;
    --border: #e4ddd3;
    --ink: #241f1a;
    --ink-soft: #6b6259;
    --accent: #7a5c8f;
    --good-bg: #e3f1e6;
    --good-ink: #1f6b3a;
    --bad-bg: #f9e6e6;
    --bad-ink: #a3312a;
    --code-bg: #eee7dc;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #17151a;
      --surface: #211e24;
      --border: #35313a;
      --ink: #eee8e0;
      --ink-soft: #a89f95;
      --accent: #b795cf;
      --good-bg: #1c3524;
      --good-ink: #7fd99a;
      --bad-bg: #3a2224;
      --bad-ink: #e79b95;
      --code-bg: #2b2730;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #17151a; --surface: #211e24; --border: #35313a; --ink: #eee8e0; --ink-soft: #a89f95;
    --accent: #b795cf; --good-bg: #1c3524; --good-ink: #7fd99a; --bad-bg: #3a2224; --bad-ink: #e79b95;
    --code-bg: #2b2730;
  }}
  :root[data-theme="light"] {{
    --bg: #f6f3ef; --surface: #ffffff; --border: #e4ddd3; --ink: #241f1a; --ink-soft: #6b6259;
    --accent: #7a5c8f; --good-bg: #e3f1e6; --good-ink: #1f6b3a; --bad-bg: #f9e6e6; --bad-ink: #a3312a;
    --code-bg: #eee7dc;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, "Segoe UI", "Noto Sans TC", sans-serif;
    margin: 0; padding: 2.5rem 1.5rem 4rem; background: var(--bg); color: var(--ink);
  }}
  h1 {{
    font-family: ui-serif, Georgia, "Noto Serif TC", serif;
    font-weight: 600; font-size: 1.9rem; text-wrap: balance;
    max-width: 760px; margin: 0 auto 0.3rem;
  }}
  .subtitle {{ max-width: 760px; margin: 0 auto 2rem; color: var(--ink-soft); font-size: 0.95rem; }}
  .card {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 1.4rem 1.6rem; margin: 0 auto 1.4rem; max-width: 760px;
  }}
  .card header {{ display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; margin-bottom: 0.6rem; }}
  .run-id {{ font-weight: 600; font-size: 0.95rem; color: var(--ink-soft); font-variant-numeric: tabular-nums; }}
  .badge {{
    padding: 0.25rem 0.8rem; border-radius: 999px; font-weight: 700; font-size: 0.78rem;
    letter-spacing: 0.04em; text-transform: uppercase; white-space: nowrap;
  }}
  .badge.good {{ background: var(--good-bg); color: var(--good-ink); }}
  .badge.bad {{ background: var(--bad-bg); color: var(--bad-ink); }}
  .meta {{ display: flex; gap: 1rem; flex-wrap: wrap; font-size: 0.82rem; color: var(--ink-soft); margin-bottom: 1rem; }}
  .meta code {{ background: var(--code-bg); padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }}
  .images {{ display: flex; gap: 0.9rem; flex-wrap: wrap; margin-bottom: 1rem; }}
  .images figure {{ margin: 0; text-align: center; flex: 1 1 220px; max-width: 240px; }}
  .images img {{
    width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 8px;
    border: 1px solid var(--border); display: block; cursor: zoom-in;
  }}
  .images .placeholder {{
    width: 100%; aspect-ratio: 1; display: flex; align-items: center; justify-content: center;
    background: var(--code-bg); border-radius: 8px; color: var(--ink-soft); font-size: 0.85rem;
  }}
  .images figcaption {{ font-size: 0.78rem; color: var(--ink-soft); margin-top: 0.4rem; letter-spacing: 0.02em; }}
  .reason {{
    font-size: 0.92rem; line-height: 1.65; border-left: 3px solid var(--accent);
    padding: 0.2rem 0 0.2rem 1rem; white-space: pre-wrap; color: var(--ink);
  }}
  #lightbox {{
    position: fixed; inset: 0; background: rgba(10, 8, 12, 0.9); display: none;
    flex-direction: column; align-items: center; justify-content: center;
    gap: 0.6rem; z-index: 1000; cursor: zoom-out;
  }}
  #lightbox.open {{ display: flex; }}
  #lightbox img {{ max-width: 92vw; max-height: 82vh; object-fit: contain; border-radius: 4px; cursor: default; }}
  #lightbox-caption {{ color: #f0ece5; font-size: 0.9rem; }}
  #lightbox-hint {{ color: #b3aca3; font-size: 0.78rem; }}
  @media (prefers-reduced-motion: no-preference) {{
    .card {{ transition: border-color 0.15s ease; }}
  }}
</style>
</head>
<body>
<h1>Nail-Transfer Pipeline Report</h1>
<p class="subtitle">{len(records)} run(s) &middot; source / reference / result &middot; judge verdict</p>
{"".join(cards)}
<div id="lightbox">
  <img id="lightbox-img" alt="">
  <div id="lightbox-caption"></div>
  <div id="lightbox-hint">&larr; &rarr; 同一組內切換圖片 &middot; &uarr; &darr; 切換到上/下一組 &middot; Esc 關閉</div>
</div>
<script>
  // Looked up from the thumbnails themselves (data-row/data-col) rather than
  // duplicated into a JSON blob -- these images are large base64 data URIs,
  // and embedding them twice roughly doubled the file size.
  const ROWS = {len(grid)};
  const COLS = {max((len(r) for r in grid), default=0)};
  const cellByPos = {{}};
  document.querySelectorAll('.images img').forEach(img => {{
    cellByPos[`${{img.dataset.row}},${{img.dataset.col}}`] = img;
  }});
  const lightbox = document.getElementById('lightbox');
  const lbImg = document.getElementById('lightbox-img');
  const lbCaption = document.getElementById('lightbox-caption');
  let cur = null;

  function render() {{
    const cell = cellByPos[`${{cur[0]}},${{cur[1]}}`];
    if (!cell) {{ lbImg.removeAttribute('src'); lbCaption.textContent = '(missing image)'; return; }}
    lbImg.src = cell.src;
    lbCaption.textContent = cell.dataset.caption;
  }}

  window.openAt = function(row, col) {{
    cur = [row, col];
    render();
    lightbox.classList.add('open');
  }};

  lightbox.addEventListener('click', (e) => {{
    if (e.target === lightbox) lightbox.classList.remove('open');
  }});

  document.addEventListener('keydown', (e) => {{
    if (!lightbox.classList.contains('open')) return;
    if (e.key === 'Escape') {{ lightbox.classList.remove('open'); return; }}
    if (e.key === 'ArrowLeft') cur[1] = (cur[1] - 1 + COLS) % COLS;
    else if (e.key === 'ArrowRight') cur[1] = (cur[1] + 1) % COLS;
    else if (e.key === 'ArrowUp') cur[0] = (cur[0] - 1 + ROWS) % ROWS;
    else if (e.key === 'ArrowDown') cur[0] = (cur[0] + 1) % ROWS;
    else return;
    e.preventDefault();
    render();
  }});
</script>
</body>
</html>"""

    out_path.write_text(html, encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--last", type=int, default=None, help="only include the most recent N runs")
    parser.add_argument("--verdict", choices=["good", "bad"], default=None,
                         help="only include runs whose judge verdict matches this")
    args = parser.parse_args()

    records = load_records(args.log)
    if args.verdict:
        records = [r for r in records if (r.get("judge") or {}).get("verdict") == args.verdict]
    if args.last:
        records = records[-args.last:]
    if not records:
        raise SystemExit(f"no records found in {args.log}")

    out_path = render_report(records, args.out)
    print(f"[done] {len(records)} run(s) -> {out_path}")


if __name__ == "__main__":
    main()
