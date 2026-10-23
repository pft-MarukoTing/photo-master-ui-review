import { t } from "./i18n";
import { useEffect, useRef, useState } from "preact/hooks";
import { api } from "./api";
import type { Box } from "./face";

export interface PreviewFace {
  detection_index: number; box: Box; face_box: Box; sent_to_model: boolean;
  label: string; role: "included" | "bystander" | "not_real_face" | "uncertain";
}
export interface PreviewPerson {
  thumbnail_box?: Box; thumbnail_candidate_id?: string | null;
  preview_person_id: string; source: "detector" | "full_image";
  detection_index: number | null; box: Box; face_box: Box | null; sent_to_model: boolean;
  label: string; role: "included" | "bystander" | "uncertain"; face_readable: boolean | null;
}
export interface PreviewStatus {
  low_confidence_state?: "ready" | "disabled" | "budget_exceeded" | "unavailable";
  format_version?: number; people?: PreviewPerson[]; augmentation_state?: "ready" | "unavailable";
  preview_job_id: string; source_sha256: string; status: string;
  state?: "pending" | "ready" | "unavailable" | "error";
  faces?: PreviewFace[]; completed_at?: number;
  reusable?: boolean;
}
type Snapshot = { key: string | null; ready: boolean; result?: PreviewStatus; error?: string };
type Load = () => Promise<{ blob: Blob; name: string }>;

// Cache in-flight POST promises so effect re-renders cannot duplicate jobs.
// Only metadata is retained, not image bytes. UI teardown stops polling, not
// the server-side job that has already been queued.
const creations = new Map<string, Promise<PreviewStatus>>();
const POLL_MS = 500;
const MAX_WAIT_MS = 120000;

export function useSubjectPreview(key: string | null, model: string, load: Load) {
  const identity = key ? `${key}#${model}` : null;
  const [state, setState] = useState<Snapshot>({ key: identity, ready: !identity });
  const current = useRef<Snapshot>({ key: identity, ready: !identity });
  const generation = useRef(0);
  // Clear synchronously on a new source, before effects/thumbnails render.
  if (current.current.key !== identity) current.current = { key: identity, ready: !identity };
  useEffect(() => {
    const token = ++generation.current;
    let alive = true;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let watchdog: ReturnType<typeof setTimeout>;
    const valid = () => alive && !stopped && generation.current === token && current.current.key === identity;
    const publish = (next: Snapshot) => {
      if (!valid()) return;
      current.current = next;
      setState(next);
    };
    if (!identity) {
      publish({ key: identity, ready: true });
      return () => { alive = false; };
    }
    publish({ key: identity, ready: false });
    watchdog = setTimeout(() => {
      publish({ key: identity, ready: true, result: current.current.result,
        error: current.current.result?.reusable ? undefined : t("人物預覽等待較久，仍可繼續聊天或分析。") });
      stopped = true;
      clearTimeout(timer);
    }, MAX_WAIT_MS);
    let created = creations.get(identity);
    if (!created) {
      const selectedAt = Date.now();
      created = load().then(({ blob, name }) => api.createPreview(blob, name, model)).then((result) => {
        console.info("subject-preview selected_to_enqueue_ms", Date.now() - selectedAt);
        return result;
      });
      creations.set(identity, created);
      if (creations.size > 50) creations.delete(creations.keys().next().value!);
    }
    const deadline = Date.now() + MAX_WAIT_MS;
    created.then((initial) => {
      if (!valid()) return;
      publish({ key: identity, ready: false, result: initial });
      const poll = async () => {
        if (!valid()) return;
        if (Date.now() >= deadline) {
          publish({ key: identity, ready: true, result: current.current.result,
                    error: current.current.result?.reusable ? undefined : t("人物預覽等待較久，仍可繼續聊天或分析。") });
          return;
        }
        try {
          let result = await api.previewStatus(initial.preview_job_id);
          if (!valid()) return;
          const done = result.status === "success" || result.status === "error";
          // Display/submission readiness is independent of artifact/job status.
          // Keep a finished preview through transient pending/error responses,
          // while still polling until the worker lifecycle ends.
          const previous = current.current.result;
          if (!result.reusable && previous?.reusable) result = { ...previous, status: result.status };
          publish({ key: identity, ready: done || !!result.reusable, result });
          if (done) {
            stopped = true;
            clearTimeout(watchdog);
            if (result.completed_at) console.info("subject-preview completion_to_ui_ms", Math.max(0, Date.now() - result.completed_at * 1000));
            return;
          }
          timer = setTimeout(poll, POLL_MS);
        } catch {
          const previous = current.current.result;
          publish({ key: identity, ready: true, result: previous,
            error: previous?.reusable ? undefined : t("人物預覽暫時不可用，仍可進行分析。") });
          stopped = true;
          clearTimeout(watchdog);
        }
      };
      timer = setTimeout(poll, POLL_MS);
    }).catch(() => {
      publish({ key: identity, ready: true, error: t("人物預覽暫時不可用，仍可進行分析。") });
      stopped = true;
      clearTimeout(watchdog);
    });
    return () => { alive = false; clearTimeout(timer); clearTimeout(watchdog); };
    // load captures the selected source; a freshly rendered callback must not
    // trigger a duplicate request. Every source/model change gets a new key.
  }, [identity]);

  const shown = state.key === identity ? state : { key: identity, ready: !identity };
  return { ...shown, identity,
    async forSubmission(_maxWaitMs = 0) {
      // Freeze only the completed result available at the click. A pending
      // job ID must never let the upload route adopt a later preview snapshot.
      if (current.current.key !== identity) throw new Error(t("照片已更換，請重新選擇分析或一鍵美化。"));
      const r = current.current.result;
      return r?.state === "ready" && r.reusable
        ? { preview_job_id: r.preview_job_id, source_sha256: r.source_sha256 } : undefined;
    },
  };
}
