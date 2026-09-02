#!/usr/bin/env python3
"""
run_inference.py -- bulk cross-combination inference for AINail checkpoints/workflows.

Unlike run_benchmark.py (which needs matched source/reference/target triples and
scores against ground truth), this script only needs a source folder and a
reference folder. It sends every (source, reference) combination through a
running ComfyUI server and saves the generated images -- no scoring, since
there's no ground-truth target to compare against.

Pairs are built as the full cross product in folder order (sorted by
filename): for source files [s1..sn] and reference files [r1..rm], pairs are
    (s1, r1), (s2, r1), ..., (sn, r1), (s1, r2), ..., (sn, r2), ...
i.e. reference is the outer loop, source is the inner loop.

Usage (run inside the docker/conda env that has requests/Pillow -- do NOT
invoke python directly outside it):
    python run_inference.py \\
        --source-dir /data/.../sources \\
        --reference-dir /data/.../references \\
        --checkpoint <subfolder>/<name>.safetensors
  where --checkpoint is the path relative to your ComfyUI's own
  models/loras/ dir. --output-dir defaults to
  ./output_<source-dir-name>_x_<reference-dir-name>_YYYYMMDD_hhmmss if not given.
"""

import argparse
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image

import comfy_client

TZ_UTC8 = timezone(timedelta(hours=8))
IMG_EXTS = (".jpg", ".jpeg", ".png")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKFLOW = REPO_ROOT / "AINail/workflows/nailSalon_workflow_20260626_api.json"
JPEG_QUALITY = 95
IMAGE_MAX_DIM = 1600


def png_bytes_to_jpeg(raw: bytes) -> bytes:
    """ComfyUI's SaveImage node always writes PNG; convert to JPEG here so
    generated/ doesn't balloon in size across repeated inference runs."""
    with Image.open(io.BytesIO(raw)) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
        return buf.getvalue()


def list_images(d: Path) -> list:
    return [p for p in sorted(d.iterdir()) if p.suffix.lower() in IMG_EXTS]


def build_combinations(source_dir: Path, reference_dir: Path) -> list:
    """Cross product in (source varies fastest, reference varies slowest) order:
    (s1,r1), (s2,r1), ..., (sn,r1), (s1,r2), ..., (sn,r2), ..."""
    sources = list_images(source_dir)
    references = list_images(reference_dir)
    return [(src, ref) for ref in references for src in sources]


def run_pair(base_url, session, template, args, source_path, reference_path, filename_prefix):
    comfy_source_name = comfy_client.upload_image(base_url, session, source_path)
    comfy_reference_name = comfy_client.upload_image(base_url, session, reference_path)

    wf = comfy_client.patch_workflow(
        template,
        source_title=args.source_title,
        reference_title=args.reference_title,
        lora_title=args.lora_title,
        source_filename=comfy_source_name,
        reference_filename=comfy_reference_name,
        checkpoint=args.checkpoint,
        seed=args.seed,
        filename_prefix=filename_prefix,
    )
    prompt_id = comfy_client.submit_prompt(base_url, session, wf)
    outputs = comfy_client.poll_until_done(base_url, session, prompt_id, timeout=args.timeout)

    output_node_id = comfy_client.find_node_by_class(wf, "SaveImage")
    images = outputs.get(output_node_id, {}).get("images", [])
    if not images:
        raise RuntimeError(f"no output images from node {output_node_id}")
    return comfy_client.fetch_output_image(base_url, session, images[0])


def _resized_copy(src_path: Path, dest_path: Path, max_dim: int) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_dim, max_dim), Image.LANCZOS)
        im.save(dest_path, "JPEG", quality=90)


def render_gallery(output_dir: Path, results: list, meta: dict) -> Path:
    assets_dir = output_dir / "report_assets"
    ok = [r for r in results if r.get("error") is None]
    failed = [r for r in results if r.get("error") is not None]

    rows = []
    grid = []
    for r in ok:
        pair_id = r["pair_id"]
        thumbs = []
        grid_row = []
        for col, (label, path) in enumerate((
            ("Source", r["source_path"]),
            ("Reference", r["reference_path"]),
            ("Generated", r["generated_path"]),
        )):
            slug = label.split()[0].lower()
            img_name = f"{pair_id}_{slug}.jpg"
            _resized_copy(Path(path), assets_dir / img_name, IMAGE_MAX_DIM)
            row_idx = len(grid)
            caption = f"{pair_id} -- {label}"
            thumbs.append(
                f'<figure><img src="report_assets/{img_name}" loading="lazy" '
                f'onclick="openLightbox({row_idx},{col})"><figcaption>{label}</figcaption></figure>'
            )
            grid_row.append({"src": f"report_assets/{img_name}", "caption": caption})
        grid.append(grid_row)

        rows.append(f"""
        <tr>
          <td>{pair_id}</td>
          <td>{r["source_stem"]}</td>
          <td>{r["reference_stem"]}</td>
          <td><div class="thumbs">{"".join(thumbs)}</div></td>
        </tr>""")

    for r in failed:
        rows.append(f"""
        <tr class="error">
          <td>{r["pair_id"]}</td>
          <td>{r["source_stem"]}</td>
          <td>{r["reference_stem"]}</td>
          <td>ERROR: {r["error"]}</td>
        </tr>""")

    html = f"""<title>AINail Inference Gallery -- {meta["run_id"]}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #fafafa; color: #1a1a1a; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .meta {{ color: #555; margin-bottom: 1.5rem; display: grid; grid-template-columns: max-content 1fr; gap: 0.3rem 0.8rem; }}
  .meta dt {{ font-weight: 600; }}
  .meta dd {{ margin: 0; word-break: break-all; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; table-layout: fixed; }}
  th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; vertical-align: top; overflow-wrap: break-word; }}
  th {{ background: #f0f0f0; position: sticky; top: 0; }}
  th:nth-child(-n+3), td:nth-child(-n+3) {{ width: 10%; }}
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
<h1>AINail Inference Gallery</h1>
<dl class="meta">
  <dt>run</dt><dd>{meta["run_id"]}</dd>
  <dt>checkpoint</dt><dd><code>{meta["checkpoint"]}</code></dd>
  <dt>workflow</dt><dd><code>{meta["workflow"]}</code></dd>
  <dt>source dir</dt><dd><code>{meta["source_dir"]}</code></dd>
  <dt>reference dir</dt><dd><code>{meta["reference_dir"]}</code></dd>
  <dt>pairs</dt><dd>{len(ok)} ok / {len(failed)} failed / {len(results)} total</dd>
</dl>
<table>
  <thead><tr><th>pair</th><th>source</th><th>reference</th><th>images</th></tr></thead>
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
    if (e.key === "ArrowLeft") cur[1] = (cur[1] + 2) % 3;
    else if (e.key === "ArrowRight") cur[1] = (cur[1] + 1) % 3;
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
    report_path = output_dir / "gallery.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--reference-dir", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True,
                         help="lora_name as ComfyUI resolves it: the path relative to that ComfyUI "
                              "instance's own models/loras/ dir (whatever subfolder it actually lives "
                              "under there -- NOT a filesystem path relative to this script or to "
                              "AINail/models/). Check your ComfyUI's models/loras/ layout, or the "
                              "lora_name dropdown in its UI, to get the right value.")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--output-dir", type=Path, default=None,
                         help="base output dir; a _YYYYMMDD_hhmmss (UTC+8) suffix is appended. "
                              "Defaults to ./output_<source-dir-name>_x_<reference-dir-name>_<checkpoint-stem>_YYYYMMDD_hhmmss "
                              "in the current directory.")
    parser.add_argument("--no-gallery", action="store_true",
                         help="skip report_assets/ and gallery.html; only write results.json. "
                              "Useful for large sweeps where you don't need to eyeball every combination.")
    parser.add_argument("--comfyui-host", default="127.0.0.1")
    parser.add_argument("--comfyui-port", type=int, default=8188, help="matches ComfyUI's --port at launch")
    parser.add_argument("--seed", type=int, default=42, help="fixed seed for reproducible cross-checkpoint comparison")
    parser.add_argument("--timeout", type=float, default=600, help="seconds to wait per prompt")
    parser.add_argument("--source-title", default="Input Image 1 (Target)")
    parser.add_argument("--reference-title", default="Input Image 2 (Reference)")
    parser.add_argument("--lora-title", default="Nail LoRA Loader")
    args = parser.parse_args()
    comfyui_url = f"http://{args.comfyui_host}:{args.comfyui_port}"

    run_id = datetime.now(TZ_UTC8).strftime("%Y%m%d_%H%M%S")
    checkpoint_stem = Path(args.checkpoint).stem
    output_base = args.output_dir if args.output_dir is not None else Path(
        f"output_{args.source_dir.name}_x_{args.reference_dir.name}_{checkpoint_stem}"
    )
    output_dir = output_base.parent / f"{output_base.name}_{run_id}"
    generated_dir = output_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    template = json.loads(args.workflow.read_text(encoding="utf-8"))
    combinations = build_combinations(args.source_dir, args.reference_dir)
    if not combinations:
        print("[error] no images found in --source-dir and/or --reference-dir", file=sys.stderr)
        sys.exit(1)
    print(f"[info] {len(combinations)} combination(s) found "
          f"({len(list_images(args.source_dir))} source x {len(list_images(args.reference_dir))} reference), "
          f"run_id={run_id}, output_dir={output_dir}")

    results = []
    with requests.Session() as session:
        for i, (source_path, reference_path) in enumerate(combinations, 1):
            pair_id = f"{source_path.stem}__{reference_path.stem}"
            print(f"[{i}/{len(combinations)}] {pair_id} ...", end=" ", flush=True)
            record = {
                "pair_id": pair_id,
                "source_stem": source_path.stem,
                "reference_stem": reference_path.stem,
                "source_path": str(source_path),
                "reference_path": str(reference_path),
                "generated_path": None,
                "error": None,
            }
            try:
                raw = run_pair(comfyui_url, session, template, args, source_path, reference_path,
                                filename_prefix=f"inference/{run_id}/{pair_id}")
                generated_path = generated_dir / f"{pair_id}.jpg"
                generated_path.write_bytes(png_bytes_to_jpeg(raw))
                record["generated_path"] = str(generated_path)
                print("ok")
            except Exception as exc:
                record["error"] = str(exc)
                print(f"FAILED: {exc}")
            results.append(record)

    (output_dir / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    ok_count = sum(1 for r in results if r["error"] is None)
    print(f"[info] {ok_count}/{len(results)} combinations succeeded")

    meta = {
        "run_id": run_id,
        "checkpoint": args.checkpoint,
        "workflow": str(args.workflow),
        "source_dir": str(args.source_dir),
        "reference_dir": str(args.reference_dir),
    }
    if args.no_gallery:
        print(f"[done] results: {output_dir / 'results.json'}")
    else:
        gallery_path = render_gallery(output_dir, results, meta)
        print(f"[done] gallery: {gallery_path}")


if __name__ == "__main__":
    main()
