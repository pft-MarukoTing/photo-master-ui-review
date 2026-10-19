import { createPortal } from "preact/compat";
import { useEffect, useState } from "preact/hooks";
import { api, urls } from "./api";
import type { Dict } from "./types";
import { Icon } from "./icons";

// Debug only: what one version's render job sent to each model, read from that
// job's stored result -- the same stages as the panel's 「Sent to GPT」 modal.
type Stage = { label: string; text?: string; image?: true; empty: boolean };

const NOT_CALLED = "（此步驟未呼叫 GPT）";

function textStage(label: string, value: unknown, fallback = NOT_CALLED): Stage {
  const text = typeof value === "string" ? value.trim() : "";
  const empty = !text || text === "–";
  return { label, text: empty ? fallback : text, empty };
}

function timingsText(timings: Dict | undefined): string {
  const entries = Object.entries(timings || {}).filter(([, s]) => s != null);
  if (!entries.length) return "";
  const width = Math.max(...entries.map(([stage]) => stage.length)) + 2;
  const lines = entries.map(([stage, s]) => `${stage.padEnd(width)}${Number(s).toFixed(2)}s`);
  const total = entries.reduce((sum, [, s]) => sum + Number(s), 0);
  lines.push("", `${"總計".padEnd(width - 2)}${total.toFixed(2)}s`);
  return lines.join("\n");
}

function stagesOf(result: Dict): Stage[] {
  const analysis: Dict = result.analysis || {};
  const prompts: Dict = analysis.prompts || {};
  const resolution = prompts.image_edit_resolution;
  const stages = [
    textStage("照片分析", prompts.photo_analysis),
    textStage("重新構圖", prompts.photo_reframe),
    textStage("使用者補充指令", prompts.user_instruction_review),
    textStage("修圖（送給 Editor）", prompts.image_edit),
    textStage(
      "Feature Resolver 結果",
      resolution ? JSON.stringify({ prompt_strategy: prompts.image_edit_strategy, ...resolution }, null, 2) : "",
    ),
    textStage("眼部修正（第二段）", prompts.gaze_repair_pass),
    { label: "第一階段中間圖（眼部修正前）", image: true as const, empty: false },
    textStage("品質檢查", prompts.quality_check),
    textStage("處理時間", timingsText(result["extra-info"]?.timings), "（尚無處理時間記錄）"),
    textStage("分析原始 JSON", JSON.stringify(analysis, null, 2), "（尚無分析結果）"),
  ];
  return stages.filter((s) => !(s.empty && (s.label === "重新構圖" || s.label === "Feature Resolver 結果")));
}

export function PromptsModal(props: { job: string; ordinal: number; source: string; onClose: () => void }) {
  const { job } = props;
  const [result, setResult] = useState<Dict | null | "failed">(null);
  const [tab, setTab] = useState(0);
  const [original, setOriginal] = useState(false);
  const [noImage, setNoImage] = useState(false);

  useEffect(() => {
    let dropped = false;
    setResult(null);
    setNoImage(false);
    api
      .result(job)
      .then((r: Dict) => !dropped && setResult(r.result || "failed"))
      .catch(() => !dropped && setResult("failed"));
    return () => {
      dropped = true;
    };
  }, [job]);

  const stages = result && result !== "failed" ? stagesOf(result) : [];
  const stage = stages[tab];

  let content;
  if (result === null) content = <pre class="prompts-modal-content">載入中…</pre>;
  else if (result === "failed") content = <pre class="prompts-modal-content">（載入這一版的結果時發生錯誤）</pre>;
  else if (stage?.image && noImage)
    content = <pre class="prompts-modal-content">（此結果沒有第一階段中間圖，可能是單階段模式或未啟用 AI 修圖）</pre>;
  else if (stage?.image)
    content = (
      <div class="prompts-modal-image-wrap">
        <button type="button" class="chat-chip prompts-modal-image-compare-btn" onClick={() => setOriginal(!original)}>
          {original ? "顯示第一階段圖" : "與原圖比較"}
        </button>
        <img
          class="prompts-modal-image-layer"
          src={original ? props.source : urls.preEditImage(job)}
          alt={original ? "原始照片" : "第一階段中間圖"}
          onError={() => !original && setNoImage(true)}
        />
      </div>
    );
  else content = <pre class="prompts-modal-content">{stage?.text}</pre>;

  return createPortal(
    <>
      <div class="sample-modal-backdrop" onClick={props.onClose} />
      <div class="sample-modal prompts-modal" role="dialog" aria-modal="true" aria-labelledby="chat-prompts-title">
        <div class="sample-modal-head">
          <h2 id="chat-prompts-title">
            第 {props.ordinal} 版 Sent to GPT <small class="prompts-modal-job">{job}</small>
          </h2>
          <button type="button" class="sample-modal-close" aria-label="關閉" onClick={props.onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div class="prompts-modal-body">
          {stages.length > 0 && (
            <div class="prompts-modal-tabs" role="tablist">
              {stages.map((s, i) => (
                <button
                  type="button"
                  role="tab"
                  aria-selected={i === tab}
                  class={`prompts-modal-tab${i === tab ? " is-active" : ""}${s.empty ? " is-empty" : ""}`}
                  onClick={() => {
                    setTab(i);
                    setOriginal(false);
                  }}
                >
                  {s.label}
                </button>
              ))}
            </div>
          )}
          {content}
        </div>
      </div>
    </>,
    document.body,
  );
}
