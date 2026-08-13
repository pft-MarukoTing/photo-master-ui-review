#!/usr/bin/env python3
"""
report.py -- render a benchmark run's per-pair metrics + thumbnails as a
single self-contained report.html under the run's output directory.
"""

import json
from pathlib import Path

from PIL import Image

IMAGE_MAX_DIM = 1600


def _resized_copy(src_path: Path, dest_path: Path, max_dim: int) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_dim, max_dim), Image.LANCZOS)
        im.save(dest_path, "JPEG", quality=90)


def _stat(values):
    values = [v for v in values if v == v and v != float("inf")]  # drop NaN/inf (exact pixel matches)
    if not values:
        return float("nan"), float("nan"), float("nan")
    return min(values), sum(values) / len(values), max(values)


def _fmt_db(value: float) -> str:
    if value != value:
        return "n/a"
    if value == float("inf"):
        return "&#8734; dB"
    return f"{value:.1f} dB"


def render_report(output_dir: Path, results: list, meta: dict) -> Path:
    """results: list of per-pair dicts with keys stem, source_path,
    reference_path, target_path, generated_path, psnr, ssim,
    nail_psnr, nail_ssim, background_psnr, background_ssim, error (None on success)."""
    assets_dir = output_dir / "report_assets"
    ok = [r for r in results if r.get("error") is None]
    failed = [r for r in results if r.get("error") is not None]

    psnr_min, psnr_mean, psnr_max = _stat([r["psnr"] for r in ok])
    ssim_min, ssim_mean, ssim_max = _stat([r["ssim"] for r in ok])
    bg_psnr_min, bg_psnr_mean, bg_psnr_max = _stat([r["background_psnr"] for r in ok])
    bg_ssim_min, bg_ssim_mean, bg_ssim_max = _stat([r["background_ssim"] for r in ok])
    nail_min, nail_mean, nail_max = _stat([r["nail_psnr"] for r in ok])
    nail_ssim_min, nail_ssim_mean, nail_ssim_max = _stat([r["nail_ssim"] for r in ok])

    # worst nail-transfer quality first (lowest PSNR) -- that's what this benchmark is graded on.
    sorted_results = sorted(ok, key=lambda r: r["nail_psnr"] if r["nail_psnr"] == r["nail_psnr"] else -float("inf"))

    rows = []
    grid = []  # grid[row_index][col_index] = {"src": ..., "caption": ...}, feeds the lightbox
    for r in sorted_results:
        stem = r["stem"]
        thumbs = []
        grid_row = []
        for col, (label, path) in enumerate((
            ("Source", r["source_path"]),
            ("Reference", r["reference_path"]),
            ("Target (GT)", r["target_path"]),
            ("Generated", r["generated_path"]),
        )):
            slug = label.split()[0].lower()
            img_name = f"{stem}_{slug}.jpg"
            _resized_copy(Path(path), assets_dir / img_name, IMAGE_MAX_DIM)
            row_idx = len(grid)
            caption = f"{stem} -- {label}"
            thumbs.append(
                f'<figure><img src="report_assets/{img_name}" loading="lazy" '
                f'onclick="openLightbox({row_idx},{col})"><figcaption>{label}</figcaption></figure>'
            )
            grid_row.append({"src": f"report_assets/{img_name}", "caption": caption})
        grid.append(grid_row)

        rows.append(f"""
        <tr>
          <td>{stem}</td>
          <td>{_fmt_db(r["psnr"])}</td>
          <td>{r["ssim"]:.4f}</td>
          <td>{_fmt_db(r["nail_psnr"])}</td>
          <td>{r["nail_ssim"]:.4f}</td>
          <td>{_fmt_db(r["background_psnr"])}</td>
          <td>{r["background_ssim"]:.4f}</td>
          <td><div class="thumbs">{"".join(thumbs)}</div></td>
        </tr>""")

    for r in failed:
        rows.append(f"""
        <tr class="error">
          <td>{r["stem"]}</td>
          <td colspan="7">ERROR: {r["error"]}</td>
        </tr>""")

    html = f"""<title>AINail Benchmark Report -- {meta["run_id"]}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #fafafa; color: #1a1a1a; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .meta {{ color: #555; margin-bottom: 1.5rem; display: grid; grid-template-columns: max-content 1fr; gap: 0.3rem 0.8rem; }}
  .meta dt {{ font-weight: 600; }}
  .meta dd {{ margin: 0; word-break: break-all; }}
  .summary {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 2rem; }}
  .card {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 0.8rem 1.2rem; min-width: 180px; }}
  .card h3 {{ margin: 0 0 0.4rem 0; font-size: 0.85rem; color: #666; text-transform: uppercase; }}
  .card .val {{ font-size: 1.4rem; font-weight: 600; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; table-layout: fixed; }}
  th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; vertical-align: top; overflow-wrap: break-word; }}
  th {{ background: #f0f0f0; position: sticky; top: 0; }}
  th:nth-child(-n+7), td:nth-child(-n+7) {{ width: 4%; }}
  th:last-child, td:last-child {{ width: auto; }}
  tr.error {{ background: #f5f5f5; color: #a00; }}
  .thumbs {{ display: flex; gap: 0.5rem; flex-wrap: wrap; }}
  .thumbs figure {{ margin: 0; text-align: center; }}
  .thumbs img {{ width: 220px; height: 220px; object-fit: cover; display: block; border-radius: 4px; cursor: zoom-in; }}
  .thumbs figcaption {{ font-size: 0.7rem; color: #666; }}
  #lightbox {{ position: fixed; inset: 0; background: rgba(0,0,0,0.85); display: flex; flex-direction: column;
    align-items: center; justify-content: center; z-index: 1000; }}
  #lightbox.hidden {{ display: none; }}
  #lightbox img {{ max-width: 92vw; max-height: 82vh; object-fit: contain; border-radius: 4px; }}
  #lightbox-caption {{ color: #eee; margin-top: 0.8rem; font-size: 0.95rem; }}
  #lightbox-hint {{ color: #aaa; margin-top: 0.3rem; font-size: 0.8rem; }}
  #lightbox-close {{ position: absolute; top: 1rem; right: 1.5rem; color: #eee; font-size: 2rem; cursor: pointer; line-height: 1; }}
</style>
<h1>AINail Benchmark Report</h1>
<dl class="meta">
  <dt>run</dt><dd>{meta["run_id"]}</dd>
  <dt>checkpoint</dt><dd><code>{meta["checkpoint"]}</code></dd>
  <dt>workflow</dt><dd><code>{meta["workflow"]}</code></dd>
  <dt>benchmark dir</dt><dd><code>{meta["benchmark_dir"]}</code></dd>
  <dt>pairs</dt><dd>{len(ok)} ok / {len(failed)} failed / {len(results)} total</dd>
</dl>
<div class="summary">
  <div class="card"><h3>PSNR (whole image)</h3><div class="val">{_fmt_db(psnr_mean)}</div><div>min {_fmt_db(psnr_min)} / max {_fmt_db(psnr_max)}</div></div>
  <div class="card"><h3>SSIM (whole image)</h3><div class="val">{ssim_mean:.4f}</div><div>min {ssim_min:.4f} / max {ssim_max:.4f}</div></div>
  <div class="card"><h3>Nail-region PSNR</h3><div class="val">{_fmt_db(nail_mean)}</div><div>min {_fmt_db(nail_min)} / max {_fmt_db(nail_max)}</div></div>
  <div class="card"><h3>Nail-region SSIM</h3><div class="val">{nail_ssim_mean:.4f}</div><div>min {nail_ssim_min:.4f} / max {nail_ssim_max:.4f}</div></div>
  <div class="card"><h3>BG PSNR</h3><div class="val">{_fmt_db(bg_psnr_mean)}</div><div>min {_fmt_db(bg_psnr_min)} / max {_fmt_db(bg_psnr_max)}</div></div>
  <div class="card"><h3>BG SSIM</h3><div class="val">{bg_ssim_mean:.4f}</div><div>min {bg_ssim_min:.4f} / max {bg_ssim_max:.4f}</div></div>
</div>
<p>Rows are sorted by <b>nail-region PSNR</b> ascending (worst nail-transfer match to GT first). PSNR is in dB: roughly &gt;40dB near-identical, 30-40dB good, 20-30dB visibly off, &lt;20dB bad. <b>Nail-region SSIM</b> is a perceptual complement to nail PSNR (can be noisier since the nail region is small). <b>Background (BG) PSNR/SSIM</b> cover everything outside the nail region (hand shape, skin, background), independent of how good the nail transfer itself looks.</p>
<table>
  <thead><tr><th>pair</th><th>PSNR</th><th>SSIM</th><th>Nail PSNR</th><th>Nail SSIM</th><th>BG PSNR</th><th>BG SSIM</th><th>images</th></tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
<div id="lightbox" class="hidden">
  <span id="lightbox-close" onclick="closeLightbox()">&times;</span>
  <img id="lightbox-img">
  <div id="lightbox-caption"></div>
  <div id="lightbox-hint">&larr; &rarr; switch image within pair &middot; &uarr; &darr; switch pair &middot; Esc to close</div>
</div>
<script>
  const GRID = {json.dumps(grid)};
  let cur = null;
  const lightbox = document.getElementById("lightbox");
  const lbImg = document.getElementById("lightbox-img");
  const lbCaption = document.getElementById("lightbox-caption");

  function renderLightbox() {{
    const cell = GRID[cur[0]][cur[1]];
    lbImg.src = cell.src;
    lbCaption.textContent = cell.caption;
  }}

  function openLightbox(row, col) {{
    cur = [row, col];
    renderLightbox();
    lightbox.classList.remove("hidden");
  }}

  function closeLightbox() {{
    lightbox.classList.add("hidden");
  }}

  document.addEventListener("keydown", (e) => {{
    if (lightbox.classList.contains("hidden")) return;
    if (e.key === "Escape") {{ closeLightbox(); return; }}
    if (e.key === "ArrowLeft") cur[1] = (cur[1] + 3) % 4;
    else if (e.key === "ArrowRight") cur[1] = (cur[1] + 1) % 4;
    else if (e.key === "ArrowUp") cur[0] = (cur[0] - 1 + GRID.length) % GRID.length;
    else if (e.key === "ArrowDown") cur[0] = (cur[0] + 1) % GRID.length;
    else return;
    e.preventDefault();
    renderLightbox();
  }});

  lightbox.addEventListener("click", (e) => {{
    if (e.target === lightbox) closeLightbox();
  }});
</script>
"""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path
