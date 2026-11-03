#!/usr/bin/env python3
"""
run_benchmark.py -- validation benchmark for aiEyeLift LoRA checkpoints, end to end on full photos.

Same procedure as AIDoubleChin's run_benchmark.py (several checkpoints side by side, fixed seed, summary.txt to pick
the best one) plus run_stages.py (every stage of the pipeline dumped at full resolution). The eye crop and the
paste-back run here (DEWE landmarks, python3.8 env); only the LoRA runs in ComfyUI.

Stages, per photo and checkpoint:
    0_analysis.json / .jpg  pre-analysis: per-eye measurements (eyes.eye_features) and decision -- keep an eye that is
                   closed or a slit (aperture < KEEP_APERTURE) or behind dark lenses (lum_ratio < KEEP_LUM), lift the
                   rest; severity from the lifted eyes' mean opening (< 0.30 = severe). Cases these rules miss (tinted
                   lenses, squints) are left to the LoRA, which was trained on keep pairs. No eye to lift = the photo is
                   returned as it is, without calling the model.
    1_source.jpg   the sRGB photo
    2_crop.png     the 1024x1024 square eyes crop sent to ComfyUI (eyes.Face.square_box, the same crop as training)
    3_lora.png     the LoRA's raw 1024x1024 output (3_lora_pass2.png etc. with several passes: the previous output fed back)
    4_output.jpg   the last pass pasted back into the photo with the eye mask (as api/paste.py): the final result
    5_diff.jpg     |4_output - 1_source| x4: should light up the upper lids only
    sheets/<checkpoint>/<stem>.jpg  everything above on one page, eyes enlarged

Scores, per checkpoint:
    vs the reference app (ground truth), AIDoubleChin's metrics: psnr/ssim over the whole photo; edit_psnr/edit_ssim inside where
        the reference changed the photo (Lab distance > --edit-lab-threshold), compared with input_edit_psnr (the untouched
        photo scored the same way); background_psnr/ssim outside it
    vs source, eye preservation (api/score_eyes.py), with the reference scored the same way for comparison:
        shift/change/color of the lower iris (should be ~0: the eyeball, gaze and iris colour don't change),
        rest = change around the eye outside the upper lid, gain = how much the eye opened (iris radii)

Output dir layout:
    output_<source-dir-name>_YYYYMMDD_hhmmss/
      stages/<checkpoint>/<stem>/1_source.jpg ... 5_diff.jpg
      result/<checkpoint>/<stem>.jpg   final result at the photo's resolution (= 4_output)
      compare/<stem>.jpg               full photo: source | each checkpoint | reference
      eyes/<stem>.jpg                  eye close-ups, same columns (left eye, then right eye)
      results.json, summary.txt

Usage (python3.8 env on the PC; ComfyUI on the H200 reached through an ssh tunnel):
    ssh -N -L 8191:127.0.0.1:8191 h200 &
    python run_benchmark.py --source-dir ../results/lora/source --target-dir ../results/lora/reference \\
        --checkpoint aiEyeLift/eye_lift_sq_20261102_000001000.safetensors \\
                     aiEyeLift/eye_lift_sq_20261102_000001250.safetensors \\
        --comfyui-port 8191 --output-dir ../results/lora/eye_lift_sq_20261102/output_val
  where each --checkpoint is the path relative to ComfyUI's models/loras/ dir, optionally with @<strength> and
  x<passes>, e.g. aiEyeLift/eye_lift_sq_20261102.safetensors@1.25x2 (defaults @1.0x1); --severe-passes N gives
  severe photos N passes whatever the spec says.
"""

import argparse
import copy
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import requests

import re

import comfy_client
import eval_metrics

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "api"))
from eyes import Face, cut, eye_features, place, tone_fix, tile  # noqa: E402
from score_eyes import KEYS as EYE_KEYS, eye_scores  # noqa: E402

TZ_UTC8 = timezone(timedelta(hours=8))
IMG_EXTS = (".jpg", ".jpeg", ".png")
DEFAULT_WORKFLOW = PROJECT_ROOT / "workflows/eyeLift_workflow_20261102_api.json"
CROP = 1024
JPEG_QUALITY = 95
SHEET_H = 1600  # compare sheets: each column scaled to at most this height (result/ and stages/ keep full resolution)
SEVERE = 0.30  # mean opening below this = severe (same split as the API prompts)
KEEP_APERTURE = 0.40  # lid to lid, iris radii: closed / slit eyes 0.05-0.32, droopy eyes that lift >= 0.63
KEEP_LUM = 0.15  # eye box / cheek brightness: dark lenses 0.10-0.12, bare eyes >= 0.27
METRICS = ["psnr", "ssim", "edit_psnr", "edit_ssim", "background_psnr", "background_ssim"]


def parse_spec(spec: str):
    """'<lora>[@strength][xpasses]' -> (lora, strength, passes, short label)."""
    m = re.fullmatch(r"(.+?\.safetensors)(?:@([\d.]+))?(?:x(\d+))?", spec)
    if not m:
        raise SystemExit(f"bad --checkpoint spec: {spec}")
    lora, strength, passes = m.group(1), float(m.group(2) or 1.0), int(m.group(3) or 1)
    name = Path(lora).stem
    if strength != 1.0 or passes != 1:
        name += f"_s{strength:g}" + (f"_x{passes}" if passes != 1 else "")
    return lora, strength, passes, name


def patch_workflow(template: dict, *, input_title: str, lora_title: str, input_filename: str,
                   checkpoint: str, strength: float, seed: int, filename_prefix: str) -> dict:
    wf = copy.deepcopy(template)
    wf[comfy_client.find_node_by_title(wf, input_title)]["inputs"]["image"] = input_filename
    wf[comfy_client.find_node_by_title(wf, lora_title)]["inputs"]["lora_name"] = checkpoint
    wf[comfy_client.find_node_by_title(wf, lora_title)]["inputs"]["strength_model"] = strength
    wf[comfy_client.find_node_by_class(wf, "KSampler")]["inputs"]["seed"] = seed
    # disk-writing SaveImage instead of the websocket one, so the result can be fetched via /history + /view
    output_id = next(k for k, n in wf.items() if n["class_type"] in ("SaveImage", "SaveImageWebsocket"))
    wf[output_id] = {
        "class_type": "SaveImage",
        "_meta": {"title": "Save Image"},
        "inputs": {"images": wf[output_id]["inputs"]["images"], "filename_prefix": filename_prefix},
    }
    return wf


def run_lora(base_url, session, template, args, crop_path: Path, checkpoint: str, strength: float,
             filename_prefix: str) -> np.ndarray:
    name = comfy_client.upload_image(base_url, session, crop_path)
    wf = patch_workflow(template, input_title=args.input_title, lora_title=args.lora_title, input_filename=name,
                        checkpoint=checkpoint, strength=strength, seed=args.seed, filename_prefix=filename_prefix)
    outputs = comfy_client.poll_until_done(base_url, session, comfy_client.submit_prompt(base_url, session, wf),
                                           timeout=args.timeout)
    images = outputs.get(comfy_client.find_node_by_class(wf, "SaveImage"), {}).get("images", [])
    if not images:
        raise RuntimeError("no output image")
    raw = comfy_client.fetch_output_image(base_url, session, images[0])
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def paste_back(src: np.ndarray, f: Face, lora: np.ndarray, box, eyes) -> np.ndarray:
    """Same as api/paste.py (mode 'eye'): the crop result on the lifted eyes + upper lids only, the rest is the source."""
    alpha = f.eye_mask(eyes)
    gen, valid = place(lora, box, src.shape)
    gen = tone_fix(src, gen, valid, alpha)
    a = (alpha * valid)[..., None]
    return np.clip(src * (1 - a) + gen * a + 0.5, 0, 255).astype(np.uint8)


def analyse(src: np.ndarray, f: Face) -> dict:
    eyes = {S: eye_features(f, src, S) for S in ("LEFT", "RIGHT")}
    for e in eyes.values():
        closed = e["aperture"] < KEEP_APERTURE
        dark = e["lum_ratio"] is not None and e["lum_ratio"] < KEEP_LUM
        e["decision"] = "keep (closed)" if closed else "keep (dark lenses)" if dark else "lift"
    lift = [S for S, e in eyes.items() if e["decision"] == "lift"]
    mean_open = sum(eyes[S]["opening"] for S in lift) / len(lift) if lift else None
    return {"eyes": eyes, "lift_eyes": lift, "mean_opening": round(mean_open, 3) if lift else None,
            "severity": "keep" if not lift else "severe" if mean_open < SEVERE else "normal"}


def analysis_image(crop: np.ndarray, f: Face, box, analysis: dict) -> np.ndarray:
    """The crop with each eye's lid landmarks, full iris circle and numbers drawn on."""
    out = crop.copy()
    k = crop.shape[1] / box[2]

    def to(p):
        return int(round((p[0] - box[0]) * k)), int(round((p[1] - box[1]) * k))

    for S in ("LEFT", "RIGHT"):
        def P(t):
            return f.pt(f"LBF_3D_{S}_EYE_{t}")
        for ring in (("OUTSIDE", "M1", "TOP", "M2", "INSIDE_OUT"), ("OUTSIDE", "M4", "BOTTOM", "M3", "INSIDE_OUT")):
            pts = sorted((to(P(t)) for t in ring), key=lambda q: q[0])  # M1..M4 are mirrored on the two eyes
            cv2.polylines(out, [np.array(pts, np.int32)], False, (0, 255, 255), 1, cv2.LINE_AA)
        c = P("CENTER")
        r = 0.5 * (np.linalg.norm(P("IRIS_LEFT") - c) + np.linalg.norm(P("IRIS_RIGHT") - c))
        cv2.circle(out, to(c), int(round(r * k)), (0, 255, 0), 1, cv2.LINE_AA)
        e = analysis["eyes"][S]
        x, y = to(P("BOTTOM"))
        y += int(0.6 * r * k)
        for i, t in enumerate((f"{S} {e['decision']}  open {e['opening']:.2f}  ap {e['aperture']:.2f}",
                               f"iris_vis {e['iris_vis']:.2f}  lum {e['lum_ratio']}  con {e['contrast']}  tex {e['tex']}")):
            for col, th in (((0, 0, 0), 3), ((255, 255, 255), 1)):
                cv2.putText(out, t, (max(x - 150, 4), y + 18 * i), 0, 0.45, col, th)
    for col, th in (((0, 0, 0), 4), ((0, 255, 255), 1)):
        cv2.putText(out, f"{analysis['severity']} (mean opening of lifted eyes {analysis['mean_opening']})", (8, 24), 0, 0.7, col, th)
    return out


def stage_sheet(stages: list, f: Face, src: np.ndarray, out: np.ndarray, tgt) -> np.ndarray:
    """Row 1: analysis | crop | each LoRA pass | |last pass - crop| x4 (512 px each).
    Row 2: per eye source | ours | reference | |ours - source| x4, enlarged."""
    t = [cv2.resize(im, (512, 512), interpolation=cv2.INTER_AREA) for im in stages]
    diff = np.clip(np.abs(stages[-1].astype(np.int16) - stages[1].astype(np.int16)) * 4, 0, 255).astype(np.uint8)
    t.append(cv2.resize(diff, (512, 512), interpolation=cv2.INTER_AREA))
    names = ["0 analysis", "2 crop"] + [f"3 lora pass {i}" for i in range(1, len(stages) - 1)] + ["|lora - crop| x4"]
    for im, n in zip(t, names):
        for col, th in (((0, 0, 0), 3), ((0, 255, 0), 1)):
            cv2.putText(im, n, (6, 500), 0, 0.6, col, th)
    row1 = np.hstack([cv2.copyMakeBorder(im, 0, 2, 0, 2, cv2.BORDER_CONSTANT, value=(255, 255, 255)) for im in t])
    d = np.clip(np.abs(out.astype(np.int16) - src.astype(np.int16)) * 4, 0, 255).astype(np.uint8)
    tiles = []
    for S in ("LEFT", "RIGHT"):
        for n, im in (("source", src), ("ours", out), ("reference", tgt), ("|ours-source| x4", d)):
            x = np.zeros((285, 450, 3), np.uint8) if im is None else cv2.resize(tile(im, f, S), (450, 285), interpolation=cv2.INTER_CUBIC)
            for col, th in (((0, 0, 0), 3), ((0, 255, 0), 1)):
                cv2.putText(x, f"{S[0]} {n}", (6, 274), 0, 0.6, col, th)
            tiles.append(cv2.copyMakeBorder(x, 0, 2, 0, 2, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    row2 = np.hstack(tiles)
    w = max(row1.shape[1], row2.shape[1])

    def pad(r):
        return cv2.copyMakeBorder(r, 0, 0, 0, w - r.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255))
    return np.vstack([pad(row1), pad(row2)])


def label(img: np.ndarray, text: str) -> np.ndarray:
    img = img.copy()
    s = max(1, img.shape[1] // 500)
    for c, th in (((0, 0, 0), 4 * s), ((255, 255, 255), s)):
        cv2.putText(img, text, (10 * s, 30 * s), 0, 0.8 * s, c, th)
    return img


def full_sheet(columns: list) -> np.ndarray:
    """columns: [(label, BGR image at the source's size or None)], left to right, scaled to SHEET_H at most."""
    h, w = next(im for _, im in columns if im is not None).shape[:2]
    k = min(1.0, SHEET_H / h)
    size = (round(w * k), round(h * k))
    gap = np.full((size[1], 8, 3), 255, np.uint8)
    out = []
    for text, im in columns:
        im = np.zeros((size[1], size[0], 3), np.uint8) if im is None else cv2.resize(im, size, interpolation=cv2.INTER_AREA)
        out += [label(im, text), gap]
    return np.hstack(out[:-1])


def eye_sheet(columns: list, f: Face) -> np.ndarray:
    tiles = []
    for S in ("LEFT", "RIGHT"):
        for text, im in columns:
            t = np.zeros((190, 300, 3), np.uint8) if im is None else tile(im, f, S)
            cv2.putText(t, f"{S[0]} {text}"[:40], (4, 182), 0, 0.45, (0, 0, 0), 3)
            cv2.putText(t, f"{S[0]} {text}"[:40], (4, 182), 0, 0.45, (0, 255, 0), 1)
            tiles.append(cv2.copyMakeBorder(t, 0, 0, 0, 2, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
    return np.hstack(tiles)


def score_vs_reference(src_rgb, tgt_rgb, out_rgb, mask) -> dict:
    return {
        "psnr": eval_metrics.global_psnr(out_rgb, tgt_rgb),
        "ssim": eval_metrics.global_ssim(out_rgb, tgt_rgb),
        "edit_psnr": eval_metrics.edit_psnr(out_rgb, tgt_rgb, mask),
        "edit_ssim": eval_metrics.edit_ssim(out_rgb, tgt_rgb, mask),
        "background_psnr": eval_metrics.background_psnr(out_rgb, tgt_rgb, mask),
        "background_ssim": eval_metrics.background_ssim(out_rgb, tgt_rgb, mask),
    }


def _finite(values):
    return [v for v in values if v is not None and v == v and v not in (float("inf"), float("-inf"))]


def eye_summary(name: str, eyes: list) -> str:
    vals = {k: _finite([e[k] for e in eyes]) for k in EYE_KEYS}
    if not vals["shift"]:
        return f"  {name}: n/a"
    return (f"  {name}: shift {statistics.mean(vals['shift']):.3f} (max {max(vals['shift']):.3f})  "
            f"change {statistics.mean(vals['change']):.2f} (max {max(vals['change']):.2f})  "
            f"color {statistics.mean(vals['color']):.2f}  rest {statistics.mean(vals['rest']):.2f}  "
            f"gain {statistics.mean(vals['gain']):+.3f} (min {min(vals['gain']):+.3f}, max {max(vals['gain']):+.3f})")


def write_summary(output_dir: Path, results: list, meta: dict, checkpoints: list) -> Path:
    lines = [f"{k}: {v}" for k, v in meta.items()]
    baseline = _finite([r["input_edit_psnr"] for r in results])
    if baseline:
        lines.append(f"input_edit_psnr (do-nothing baseline): {sum(baseline) / len(baseline):.4f} dB")
    lines += ["", "eye preservation vs source (lower is better except gain; reference = the bar):",
              eye_summary("reference", [e for r in results for e in r["reference_eyes"].values()])]
    for ckpt in checkpoints:
        lines.append(eye_summary(parse_spec(ckpt)[3], [e for r in results for e in r["checkpoints"][ckpt].get("eyes", {}).values()]))
    # per photo, which checkpoint is closest to the reference in the edited area
    wins = {c: 0 for c in checkpoints}
    for r in results:
        ok = [(r["checkpoints"][c]["edit_psnr"], c) for c in checkpoints if r["checkpoints"][c].get("error") is None
              and _finite([r["checkpoints"][c].get("edit_psnr")])]
        if ok:
            wins[max(ok)[1]] += 1
    for ckpt in checkpoints:
        rows = [r["checkpoints"][ckpt] for r in results]
        ok = [row for row in rows if row.get("error") is None]
        lines.append("")
        lines.append(f"[{ckpt}] {len(ok)} ok / {len(rows) - len(ok)} failed, closest to the reference (edit_psnr) on {wins[ckpt]} / {len(results)}")
        for key in METRICS:
            vals = _finite([row.get(key) for row in ok])
            if not vals:
                lines.append(f"  {key}: n/a")
                continue
            worst_val, worst_stem = min(((row[key], r["stem"]) for r, row in zip(results, rows)
                                         if row.get("error") is None and row.get(key) in vals), key=lambda p: p[0])
            unit = " dB" if "psnr" in key else ""
            lines.append(f"  {key}: {sum(vals) / len(vals):.4f}{unit} (worst {worst_val:.4f}{unit} @ {worst_stem})")
    path = output_dir / "summary.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-dir", required=True, type=Path, help="sRGB photos")
    parser.add_argument("--target-dir", required=True, type=Path, help="reference app results, same file names")
    parser.add_argument("--checkpoint", required=True, nargs="+",
                        help="one or more LoRA paths relative to ComfyUI's models/loras/ dir, compared side by side; "
                             "optional @<strength> and x<passes> suffixes")
    parser.add_argument("--severe-passes", type=int, default=None,
                        help="passes for severe photos (mean opening < 0.30), overriding the spec's x<passes>")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="defaults to ./output_<source-dir-name>; a _YYYYMMDD_hhmmss suffix is always appended")
    parser.add_argument("--comfyui-host", default="127.0.0.1")
    parser.add_argument("--comfyui-port", type=int, default=8191)
    parser.add_argument("--seed", type=int, default=42, help="fixed seed for reproducible cross-checkpoint comparison")
    parser.add_argument("--timeout", type=float, default=600, help="seconds to wait per prompt")
    parser.add_argument("--edit-lab-threshold", type=float, default=5.0,
                        help="Lab-distance threshold used to derive the edited region from source/reference")
    parser.add_argument("--input-title", default="Input Image")
    parser.add_argument("--lora-title", default="Eye Lift LoRA Loader")
    args = parser.parse_args()
    url = f"http://{args.comfyui_host}:{args.comfyui_port}"

    run_id = datetime.now(TZ_UTC8).strftime("%Y%m%d_%H%M%S")
    base = args.output_dir if args.output_dir is not None else Path(f"output_{args.source_dir.name}")
    output_dir = base.parent / f"{base.name}_{run_id}"
    template = json.loads(args.workflow.read_text(encoding="utf-8"))
    photos = sorted(p for p in args.source_dir.iterdir() if p.suffix.lower() in IMG_EXTS)
    print(f"[info] {len(photos)} photo(s) x {len(args.checkpoint)} checkpoint(s), run_id={run_id}, output_dir={output_dir}")

    results = []
    with requests.Session() as session:
        for i, src_path in enumerate(photos, 1):
            stem = src_path.stem
            src = cv2.imread(str(src_path))
            tgt_path = args.target_dir / src_path.name
            tgt = cv2.imread(str(tgt_path)) if tgt_path.exists() else None
            if tgt is not None and tgt.shape != src.shape:
                tgt = cv2.resize(tgt, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_AREA)
            f = Face(src)
            box = f.square_box()
            c = cut(src, box)
            crop = cv2.resize(c, (CROP, CROP), interpolation=cv2.INTER_CUBIC if box[2] < CROP else cv2.INTER_AREA)
            src_rgb = src[..., ::-1]
            analysis = analyse(src, f)
            record = {"stem": stem, "source_path": str(src_path), "target_path": str(tgt_path) if tgt is not None else None,
                      "box": box, "opening": {S: round(f.opening(S), 3) for S in ("LEFT", "RIGHT")}, "analysis": analysis,
                      "checkpoints": {}}
            analysis_img = analysis_image(crop, f, box, analysis)
            mask = None
            if tgt is not None:
                mask = eval_metrics.edit_mask_from_control_target(src_rgb, tgt[..., ::-1], args.edit_lab_threshold)
                record["edit_area_ratio"] = float(mask.mean())
                record["input_edit_psnr"] = eval_metrics.edit_psnr(src_rgb, tgt[..., ::-1], mask)
                record["reference_eyes"] = eye_scores(src, tgt, f)
            else:
                record["input_edit_psnr"] = None; record["reference_eyes"] = {}
            columns = [("source", src)]
            for spec in args.checkpoint:
                ckpt, strength, passes, name = parse_spec(spec)
                if args.severe_passes and analysis["severity"] == "severe":
                    passes = args.severe_passes
                stage = output_dir / "stages" / name / stem
                stage.mkdir(parents=True, exist_ok=True)
                (stage / "0_analysis.json").write_text(json.dumps(analysis, indent=1), encoding="utf-8")
                cv2.imwrite(str(stage / "0_analysis.jpg"), analysis_img, [cv2.IMWRITE_JPEG_QUALITY, 92])
                cv2.imwrite(str(stage / "1_source.jpg"), src, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                cv2.imwrite(str(stage / "2_crop.png"), crop)
                print(f"[{i}/{len(photos)}] {stem} @ {name} ({passes} pass) ...", end=" ", flush=True)
                row = {"error": None, "strength": strength, "passes": passes}
                out = None
                try:
                    inp, lora_stages = stage / "2_crop.png", [analysis_img, crop]
                    if not analysis["lift_eyes"]:
                        passes = 0  # nothing to lift: return the source, no model call
                        lora = crop
                    for p in range(1, passes + 1):
                        lora = run_lora(url, session, template, args, inp, ckpt, strength,
                                        f"eyelift_benchmark/{run_id}/{name}/{stem}")
                        inp = stage / ("3_lora.png" if p == 1 else f"3_lora_pass{p}.png")
                        cv2.imwrite(str(inp), lora)
                        lora_stages.append(lora)
                    out = paste_back(src, f, lora, box, analysis["lift_eyes"]) if passes else src.copy()
                    row["passes"] = passes
                    cv2.imwrite(str(stage / "4_output.jpg"), out, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                    diff = np.clip(np.abs(out.astype(np.int16) - src.astype(np.int16)) * 4, 0, 255).astype(np.uint8)
                    cv2.imwrite(str(stage / "5_diff.jpg"), diff, [cv2.IMWRITE_JPEG_QUALITY, 90])
                    res_path = output_dir / "result" / name / f"{stem}.jpg"
                    res_path.parent.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(res_path), out, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                    row["result_path"] = str(res_path)
                    sheet = output_dir / "sheets" / name / f"{stem}.jpg"
                    sheet.parent.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(sheet), stage_sheet(lora_stages, f, src, out, tgt), [cv2.IMWRITE_JPEG_QUALITY, 90])
                    row["eyes"] = eye_scores(src, out, f)
                    if tgt is not None:
                        row.update(score_vs_reference(src_rgb, tgt[..., ::-1], out[..., ::-1], mask))
                    e = row["eyes"]
                    print("shift " + " ".join(f"{S[0]}{e[S]['shift']:.2f}" for S in e) +
                          " gain " + " ".join(f"{S[0]}{e[S]['gain']:+.2f}" for S in e) +
                          (f" edit_psnr={row['edit_psnr']:.1f}dB (input {record['input_edit_psnr']:.1f}dB)" if tgt is not None else ""))
                except Exception as exc:
                    row["error"] = str(exc)
                    print(f"FAILED: {exc}")
                record["checkpoints"][spec] = row
                columns.append((name.replace("eye_lift_sq_20261102_", "step "), out))
            columns.append(("reference", tgt))
            (output_dir / "compare").mkdir(parents=True, exist_ok=True)
            (output_dir / "eyes").mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(output_dir / "compare" / f"{stem}.jpg"), full_sheet(columns), [cv2.IMWRITE_JPEG_QUALITY, 92])
            cv2.imwrite(str(output_dir / "eyes" / f"{stem}.jpg"), eye_sheet(columns, f), [cv2.IMWRITE_JPEG_QUALITY, 92])
            results.append(record)

    (output_dir / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    meta = {"run_id": run_id, "workflow": str(args.workflow), "source_dir": str(args.source_dir),
            "target_dir": str(args.target_dir), "seed": args.seed}
    summary_path = write_summary(output_dir, results, meta, args.checkpoint)
    print(summary_path.read_text(encoding="utf-8"))
    print(f"[done] summary: {summary_path}, full-photo comparisons: {output_dir / 'compare'}")


if __name__ == "__main__":
    main()
