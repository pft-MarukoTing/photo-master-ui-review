#!/usr/bin/env python3
"""Run the complete Luna body-analysis -> image-edit pipeline and build HTML.

The default is deliberately body-focused: every production Luna analysis pass
still runs and remains visible in the report, but non-body findings are disabled
before Step 5 so the generated before/after is useful for evaluating
``photo_analysis_body_reshape`` itself.

Examples:

    python3 test_body_reshape_api_to_edit.py demo_images/test_0901.json

    python3 test_body_reshape_api_to_edit.py demo_images/test_0901.json \
        --output body_reshape_api_to_edit/my_batch_0904_test_0901

Hold the comparison button to show Before; release it to return to After.
Use ``--include-other-edits`` when a production-like edit containing the other
analysis-approved corrections is desired. Use ``--skip-qa`` to omit Step 6.
"""

from __future__ import annotations

import argparse
import base64
import copy
import html
import json
import mimetypes
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_ENV_FILE = Path(__file__).resolve().with_name(".env.default")
DEFAULT_ANALYSIS_MODEL = "gpt-5.6-luna"
DEFAULT_IMAGE_MODEL = "gpt-image-2"


def _load_env_file(path: Path) -> None:
    """Load dotenv-style settings before importing the production pipeline."""
    if not path.is_file():
        raise FileNotFoundError(f"找不到環境設定檔：{path}")
    loaded_key = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key] = value
        if key == "OPENAI_API_KEY" and value:
            loaded_key = True
    if not loaded_key and not os.getenv("OPENAI_API_KEY", "").strip():
        raise RuntimeError(f"{path} 中沒有有效的 OPENAI_API_KEY")


def _read_image_paths(input_path: Path) -> list[Path]:
    if input_path.suffix.lower() != ".json":
        return [input_path]

    data = json.loads(input_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = next(
            (data[key] for key in ("images", "image_paths", "files") if isinstance(data.get(key), list)),
            None,
        )
        if items is None:
            raise ValueError("JSON 必須是圖片路徑陣列，或包含 images/image_paths/files 陣列")
    else:
        raise ValueError("JSON 必須是圖片路徑陣列")

    paths = []
    for index, item in enumerate(items):
        if isinstance(item, str):
            raw_path = item
        elif isinstance(item, dict):
            raw_path = next(
                (item.get(key) for key in ("image", "image_path", "path", "file") if item.get(key)),
                None,
            )
        else:
            raw_path = None
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"JSON 第 {index + 1} 項不是有效圖片路徑")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            beside_manifest = (input_path.parent / path).resolve()
            from_cwd = (Path.cwd() / path).resolve()
            path = beside_manifest if beside_manifest.exists() else from_cwd
        else:
            path = path.resolve()
        paths.append(path)
    if not paths:
        raise ValueError("JSON 沒有任何圖片路徑")
    return paths


def _output_paths(output_arg: Path | None) -> tuple[Path, Path]:
    if output_arg:
        resolved = output_arg.expanduser().resolve()
        if resolved.suffix.lower() == ".html":
            return resolved.parent, resolved
        return resolved, resolved / "index.html"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = (Path.cwd() / "body_reshape_api_to_edit" / stamp).resolve()
    return output_dir, output_dir / "index.html"


def _body_focused_analysis(analysis: dict) -> dict:
    """Keep all diagnostics but authorize only body key points at edit time."""
    focused = copy.deepcopy(analysis)
    options = dict(focused.get("_advanced_options") or {})
    options.update({
        "gaze_contact_detection": False,
        "eye_closed_detection": False,
        "hair_mess_detection": False,
        "smile_adjustment": False,
        "calm_hair": False,
        # The compact resolver has one explicit contract per body target and
        # is therefore easier to audit than the broad legacy diagnosis prose.
        "editor_prompt_strategy": "resolved_compact",
    })
    focused["_advanced_options"] = options
    focused["correction_plan"] = []
    focused["correction_axes"] = []
    focused["correction_inverse"] = []
    focused["detected_issues"] = []
    focused["retouch_recipe"] = []
    focused["rejected_corrections"] = []
    focused["rejected_axes"] = []
    focused["rejected_preserve"] = []
    focused["style_options"] = {}
    return focused


def _run_one(record: dict, pipeline_module, args, assets_dir: Path, index: int) -> None:
    from prompts import body_reshape_items

    image_path = record["image_path"]
    if not image_path.is_file():
        raise FileNotFoundError(f"找不到圖片：{image_path}")
    image_bytes = image_path.read_bytes()

    analysis_options = {
        "gaze_contact_detection": args.include_other_edits,
        "eye_closed_detection": args.include_other_edits,
        "hair_mess_detection": args.include_other_edits,
        "smile_adjustment": args.include_other_edits,
        "editor_prompt_strategy": "resolved_compact",
    }
    started = time.monotonic()
    analysis = pipeline_module.analyze_stage(
        image_bytes,
        aspect_ratio=args.aspect_ratio,
        advanced_options=analysis_options,
        analysis_model=args.analysis_model,
    )
    record["analysis_seconds"] = time.monotonic() - started
    record["analysis"] = analysis
    record["body_items"] = body_reshape_items(analysis)

    edit_analysis = analysis if args.include_other_edits else _body_focused_analysis(analysis)
    record["edit_analysis"] = edit_analysis

    started = time.monotonic()
    finished = pipeline_module.finish_stage(
        image_bytes=image_bytes,
        analysis=edit_analysis,
        user_prompt="",
        preset_key=args.preset,
        quality=args.quality,
        ai_photo_editing=True,
        image_model=args.image_model,
        analysis_model=args.analysis_model,
        run_qa=not args.skip_qa,
        aspect_ratio=args.aspect_ratio,
        overwrite_prompt=False,
        single_pass_edit=True,
    )
    record["edit_seconds"] = time.monotonic() - started
    record["finished"] = finished

    image_b64 = finished.get("result_image") or ""
    if not image_b64:
        raise RuntimeError("編輯 API 沒有回傳結果圖片")
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", image_path.stem).strip("._") or "image"
    output_name = f"{index + 1:03d}_{safe_stem}_after.png"
    output_path = assets_dir / output_name
    output_path.write_bytes(base64.b64decode(image_b64))
    record["after_relative_path"] = f"assets/{output_name}"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False, default=str)


def _source_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _details(title: str, value, *, open_by_default: bool = False) -> str:
    if value in (None, "", [], {}):
        content = '<span class="muted">本次沒有內容／沒有執行</span>'
    elif isinstance(value, str):
        content = f"<pre>{html.escape(value)}</pre>"
    else:
        content = f"<pre>{html.escape(_json(value))}</pre>"
    open_attr = " open" if open_by_default else ""
    return f'<details{open_attr}><summary>{html.escape(title)}</summary>{content}</details>'


def _body_cards(analysis: dict) -> str:
    cards = []
    for subject_index, subject in enumerate(analysis.get("subjects") or []):
        if not isinstance(subject, dict) or not subject.get("is_person"):
            continue
        plan = subject.get("body_reshape") or {}
        points = plan.get("key_points") or []
        point_html = (
            "<ul>" + "".join(f"<li>{html.escape(str(point))}</li>" for point in points) + "</ul>"
            if points else '<p class="muted">沒有明確可執行的 body key point。</p>'
        )
        cards.append(f"""
<section class="person-card">
  <h3>{html.escape(subject.get('label') or f'人物 {subject_index}')}</h3>
  <p><strong>subject_index:</strong> {subject_index} · <strong>local_detection_index:</strong> {html.escape(str(subject.get('local_detection_index', -1)))}</p>
  <div class="report">{html.escape(str(plan.get('report') or '')).replace(chr(10), '<br>')}</div>
  <h4>Edit 實際使用的 key points</h4>{point_html}
</section>""")
    return "\n".join(cards) or '<div class="notice">本張照片沒有辨識到可分析人物。</div>'


def _comparison(record: dict, panel_id: str) -> str:
    path = record["image_path"]
    try:
        before_src = _source_data_url(path)
    except Exception as exc:
        return f'<div class="error">無法讀取 Before：{html.escape(str(exc))}</div>'

    after_path = record.get("after_relative_path")
    if not after_path:
        return f"""
<div class="compare-frame"><img class="compare-layer before-only" src="{before_src}" alt="Before"></div>
<button class="hold-before" type="button" disabled>沒有 After 可比較</button>"""
    return f"""
<div class="compare-frame" id="compare-{panel_id}">
  <img class="compare-layer after-layer" src="{html.escape(after_path)}" alt="After">
  <img class="compare-layer before-layer" src="{before_src}" alt="Before">
  <span class="compare-badge">目前：<span>After</span></span>
</div>
<button class="hold-before" type="button" data-compare="compare-{panel_id}">
  按住顯示 Before，放開回到 After
</button>
<p class="compare-help">支援滑鼠、觸控，以及鍵盤 Space/Enter。</p>"""


def _result_fragment(record: dict, index: int) -> str:
    panel_id = f"photo-{index}"
    path = record["image_path"]
    error = record.get("error")
    analysis = record.get("analysis") or {}
    finished = record.get("finished") or {}
    result_text = finished.get("result_text") or {}
    final_analysis = result_text.get("analysis") or {}
    prompts = final_analysis.get("prompts") or analysis.get("prompts") or {}
    qa = result_text.get("quality_check") or {}
    token_usage = result_text.get("token_usage") or {}
    extra = result_text.get("extra-info") or {}
    error_html = (
        f'<div class="error"><strong>執行錯誤：</strong><pre>{html.escape(str(error))}</pre></div>'
        if error else ""
    )
    mode = "完整 production corrections" if record.get("include_other_edits") else "Body-only edit（其他分析僅保留、不執行）"
    prompt_details = "".join([
        _details("photo_analysis", prompts.get("photo_analysis")),
        _details("photo_analysis_portrait_qc", prompts.get("photo_analysis_portrait_qc")),
        _details("photo_analysis_eye_repair_planning", prompts.get("photo_analysis_eye_repair_planning")),
        _details("photo_analysis_body_reshape", prompts.get("photo_analysis_body_reshape")),
        _details("實際送入 Edit API 的 prompt", prompts.get("image_edit")),
        _details("Edit raw/legacy prompt", prompts.get("image_edit_raw")),
        _details("Edit prompt resolution metadata", prompts.get("image_edit_resolution")),
        _details("Quality-check prompt", prompts.get("quality_check")),
    ])
    hidden = " hidden" if index else ""
    return f"""
<article class="photo-panel" id="{panel_id}"{hidden}>
  <h2>{index + 1}. {html.escape(path.name)}</h2>
  <p><strong>模式：</strong>{mode}<br><strong>圖片：</strong>{html.escape(str(path))}</p>
  {error_html}
  <div class="columns">
    <aside class="compare-column">
      <section class="panel"><h3>Before / After</h3>{_comparison(record, panel_id)}</section>
    </aside>
    <div class="results-column">
      <section class="panel"><h3>Body reshape 分析與 Edit 指令</h3>{_body_cards(analysis)}</section>
      <section class="panel"><h3>完整 API prompts</h3>{prompt_details}</section>
      <section class="panel"><h3>Quality check</h3>{_details("QA result", qa, open_by_default=True)}</section>
      <section class="panel"><h3>執行資訊</h3>
        <p>Analyze: {record.get('analysis_seconds', 0):.2f}s · Edit + QA: {record.get('edit_seconds', 0):.2f}s<br>
        Analysis model: {html.escape(str(record.get('analysis_model')))} · Image model: {html.escape(str(record.get('image_model')))}</p>
        {_details("Timings / model info", extra)}
        {_details("Token usage", token_usage)}
      </section>
      <section class="panel"><h3>Debug JSON</h3>
        {_details("原始 analyze_stage 結果", analysis)}
        {_details("實際送入 finish_stage 的 analysis", record.get('edit_analysis') or {})}
      </section>
    </div>
  </div>
</article>"""


def _pending_fragment(record: dict, index: int) -> str:
    hidden = " hidden" if index else ""
    path = record["image_path"]
    return f"""
<article class="photo-panel" id="photo-{index}"{hidden}>
  <h2>{index + 1}. {html.escape(path.name)}</h2>
  <section class="panel notice">
    <h3>執行中</h3>
    <p>此圖片正在等待 worker 或執行 analyze → edit → QA。</p>
  </section>
</article>"""


_STYLE = """
* { box-sizing: border-box; }
body { margin: 0; padding: 24px; color: #202124; background: #f5f6f8; font: 15px/1.55 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width: 1600px; margin: auto; }
h1 { margin-top: 0; } h2 { margin-top: 0; } h3 { margin: 0 0 10px; } h4 { margin-bottom: 6px; }
.photo-tabs { position: sticky; top: 0; z-index: 5; display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 0; margin-bottom: 16px; background: #f5f6f8; }
.photo-tab { cursor: pointer; border: 1px solid #aab3c0; border-radius: 999px; padding: 8px 14px; background: white; color: #253044; }
.photo-tab:hover, .photo-tab.active { background: #315efb; color: white; border-color: #315efb; }
.photo-panel[hidden] { display: none; }
.columns { display: grid; grid-template-columns: minmax(360px, .9fr) minmax(520px, 1.7fr); gap: 20px; align-items: start; }
.compare-column { position: sticky; top: 78px; align-self: start; }
.results-column { min-width: 0; }
.panel, .person-card { background: white; border-radius: 12px; padding: 18px; box-shadow: 0 1px 5px #0001; margin-bottom: 20px; }
.person-card { border: 1px solid #e3e7ee; box-shadow: none; }
.compare-frame { position: relative; width: 100%; height: min(68vh, 760px); min-height: 420px; overflow: hidden; border-radius: 9px; background: #17191d; user-select: none; }
.compare-layer { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
.before-layer { opacity: 0; transition: opacity 80ms linear; }
.compare-frame.show-before .before-layer { opacity: 1; }
.compare-badge { position: absolute; top: 10px; left: 10px; z-index: 2; padding: 5px 9px; border-radius: 999px; color: white; background: #000b; font-size: 13px; }
.hold-before { width: 100%; margin-top: 12px; padding: 12px 14px; border: 0; border-radius: 8px; color: white; background: #315efb; font-weight: 700; cursor: pointer; touch-action: none; }
.hold-before:active, .hold-before.is-held { background: #1739a8; transform: translateY(1px); }
.hold-before:disabled { background: #8b929e; cursor: not-allowed; }
.compare-help, .muted { color: #70757d; }
.compare-help { margin: 7px 0 0; font-size: 13px; }
.report { padding: 12px; border-radius: 8px; background: #f7f8fa; }
details { margin: 8px 0; border-top: 1px solid #eceff3; padding-top: 8px; }
summary { cursor: pointer; font-weight: 650; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 620px; overflow: auto; padding: 12px; border-radius: 8px; background: #f0f2f5; }
.notice { padding: 12px; border-radius: 8px; background: #fff7d6; }
.error { margin-bottom: 18px; padding: 12px; border: 1px solid #e88; border-radius: 8px; background: #ffe8e8; }
ul { margin-top: 5px; padding-left: 22px; }
@media (max-width: 920px) { body { padding: 12px; } .columns { grid-template-columns: 1fr; } .compare-column { position: static; } .compare-frame { height: 65vh; min-height: 320px; } }
"""


_SCRIPT = """
<script>
(function () {
  function showBefore(button, show) {
    var frame = document.getElementById(button.dataset.compare);
    if (!frame) return;
    frame.classList.toggle('show-before', show);
    button.classList.toggle('is-held', show);
    var status = frame.querySelector('.compare-badge span');
    if (status) status.textContent = show ? 'Before' : 'After';
  }
  document.querySelectorAll('.hold-before[data-compare]').forEach(function (button) {
    button.addEventListener('pointerdown', function (event) {
      event.preventDefault();
      try { button.setPointerCapture(event.pointerId); } catch (_) {}
      showBefore(button, true);
    });
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(function (name) {
      button.addEventListener(name, function () { showBefore(button, false); });
    });
    button.addEventListener('keydown', function (event) {
      if (event.key === ' ' || event.key === 'Enter') {
        event.preventDefault(); showBefore(button, true);
      }
    });
    button.addEventListener('keyup', function (event) {
      if (event.key === ' ' || event.key === 'Enter') {
        event.preventDefault(); showBefore(button, false);
      }
    });
    button.addEventListener('blur', function () { showBefore(button, false); });
  });
  document.querySelectorAll('.photo-tab').forEach(function (button) {
    button.addEventListener('click', function () {
      var target = button.dataset.target;
      var scrollPosition = window.scrollY;
      document.querySelectorAll('.photo-panel').forEach(function (panel) {
        panel.hidden = panel.id !== target;
      });
      document.querySelectorAll('.photo-tab').forEach(function (item) {
        item.classList.toggle('active', item === button);
      });
      // Switching photos should replace the visible result in place rather
      // than sending the reader back to the beginning of the report.
      window.scrollTo({top: scrollPosition, behavior: 'auto'});
      window.requestAnimationFrame(function () {
        window.scrollTo({top: scrollPosition, behavior: 'auto'});
      });
    });
  });
})();
</script>
"""


def _write_html(output_path: Path, records: list[dict]) -> None:
    buttons = []
    fragments = []
    for index, record in enumerate(records):
        state = record.get("status")
        status = "執行中" if state == "running" else "失敗" if record.get("error") else "完成"
        label = f"{index + 1}. {record['image_path'].name}（{status}）"
        buttons.append(
            f'<button class="photo-tab{" active" if index == 0 else ""}" '
            f'data-target="photo-{index}" type="button">{html.escape(label)}</button>'
        )
        fragments.append(
            _pending_fragment(record, index)
            if state == "running" else _result_fragment(record, index)
        )
    generated = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    document = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Body reshape API to Edit test</title><style>{_STYLE}</style></head><body><main>
<h1>Body reshape API → Edit 完整測試</h1>
<p><strong>產生時間：</strong>{html.escape(generated)}<br>
圖片預設顯示 <strong>After</strong>；按住左側按鈕時顯示 <strong>Before</strong>，放開後恢復 After。</p>
<nav class="photo-tabs" aria-label="照片切換">{''.join(buttons)}</nav>
{''.join(fragments)}
{_SCRIPT}
</main></body></html>"""
    output_path.write_text(document, encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run full Luna analysis and body-focused image edit, then write a before/after HTML report."
    )
    parser.add_argument("image_or_json", type=Path, help="input image path or JSON manifest")
    parser.add_argument("-o", "--output", type=Path, help="output directory or index.html path")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE, help="dotenv file (default: .env.default)")
    parser.add_argument("--analysis-model", default=DEFAULT_ANALYSIS_MODEL)
    parser.add_argument("--image-model", default=DEFAULT_IMAGE_MODEL)
    parser.add_argument("--quality", choices=("low", "medium", "high"), default="medium")
    parser.add_argument("--preset", choices=("natural", "editorial", "product", "portrait", "custom"), default="natural")
    parser.add_argument(
        "--aspect-ratio",
        default="original",
        help="default original, so crop/reframing does not obscure the body comparison",
    )
    parser.add_argument("--skip-qa", action="store_true", help="skip finish_stage quality-check API call")
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="number of images processed concurrently for JSON batches (default: 4)",
    )
    parser.add_argument(
        "--include-other-edits",
        action="store_true",
        help="also execute overall/portrait/eye/hair/smile corrections; default executes body key points only",
    )
    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers 必須大於或等於 1")
    input_path = args.image_or_json.expanduser().resolve()
    if not input_path.is_file():
        parser.error(f"輸入不存在：{input_path}")
    try:
        _load_env_file(args.env_file.expanduser().resolve())
        image_paths = _read_image_paths(input_path)
    except Exception as exc:
        parser.error(str(exc))

    # Import only after .env.default is loaded: the production logger and API
    # clients read their environment during module import.
    try:
        import pipeline as pipeline_module
    except Exception as exc:
        parser.error(f"無法載入 production pipeline：{exc}")

    output_dir, output_path = _output_paths(args.output)
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for index, image_path in enumerate(image_paths):
        records.append({
            "image_path": image_path,
            "analysis_model": args.analysis_model,
            "image_model": args.image_model,
            "include_other_edits": args.include_other_edits,
            "status": "running",
        })

    worker_count = min(args.workers, len(records))
    print(f"批次啟動：{len(records)} 張圖片，workers={worker_count}", flush=True)
    _write_html(output_path, records)
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="body-reshape") as executor:
        futures = {}
        for index, record in enumerate(records):
            print(f"[{index + 1}/{len(records)}] 排入 analyze → edit：{record['image_path']}", flush=True)
            future = executor.submit(_run_one, record, pipeline_module, args, assets_dir, index)
            futures[future] = (index, record)

        completed = 0
        for future in as_completed(futures):
            index, record = futures[future]
            completed += 1
            try:
                future.result()
                record["status"] = "complete"
                points = sum(
                    len(item.get("key_points") or [])
                    for item in record.get("body_items") or []
                )
                print(
                    f"[{completed}/{len(records)} 完成] #{index + 1} "
                    f"{record['image_path'].name}：body key points={points}",
                    flush=True,
                )
            except Exception as exc:
                record["error"] = str(exc)
                record["status"] = "failed"
                print(
                    f"[{completed}/{len(records)} 完成] #{index + 1} "
                    f"{record['image_path'].name} 失敗：{exc}",
                    file=sys.stderr,
                    flush=True,
                )
            # Only the main thread writes the report. Records remain in the
            # manifest order even though workers finish out of order.
            _write_html(output_path, records)

    failed = sum(bool(record.get("error")) for record in records)
    print(f"批次完成：{len(records) - failed}/{len(records)} 張成功")
    print(f"{output_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
