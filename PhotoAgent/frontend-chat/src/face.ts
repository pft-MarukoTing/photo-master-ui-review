// Port of the old review card's portrait-check model (results.js
// faceIssueEntries / subjectForDetection / getFaceIssueOverrides), minus body
// reshape, which stays off in Stage 1.
import { useEffect, useState } from "preact/hooks";
import { api } from "./api";
import type { Analysis, Dict, FaceOverride } from "./types";

// Named for what was found, not for the finding of it: by the time a chip is on
// screen the detection has happened, and the dot on the chip says so. The switches
// in 人像修正 are asked before there is a photo and carry the longer
// "we will fix this" names instead -- a different question, a different label.
export const FACE_CATEGORIES = [
  { field: "blink_issue", label: "閉眼", flagField: "should_fix", option: "eye_closed_detection" },
  { field: "gaze_issue", label: "視線", flagField: "should_fix", option: "gaze_contact_detection" },
  { field: "hair_issue", label: "髮絲", flagField: "should_fix", option: "hair_mess_detection" },
  { field: "smile_issue", label: "笑容", flagField: "adjust_safe", option: "smile_adjustment" },
] as const;

export const LEVEL_OPTIONS: Record<string, { value: string; label: string }[]> = {
  hair_issue: [
    { value: "dynamic", label: "保留髮絲動態" },
    { value: "tidy", label: "梳整俐落" },
  ],
  smile_issue: [
    { value: "auto", label: "自動" },
    { value: "neutral", label: "不笑" },
    { value: "gentle", label: "微笑" },
    { value: "broad", label: "開朗笑" },
    { value: "laughing", label: "大笑" },
  ],
};

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FaceIssue {
  key: string; // "<subjectIndex>:<category>"
  category: string;
  label: string;
  option: string;
  flagged: boolean;
  allowed: boolean;
  level: string | null;
  levelUserTouched: boolean;
}

export interface FaceEntry {
  box: Box;
  subjectIndex: number;
  label: string;
  issues: FaceIssue[];
}

const finiteBox = (b: Dict | null | undefined): b is Box =>
  !!b && [b.x, b.y, b.w, b.h].every(Number.isFinite);

function boxOverlapCoefficient(a: Box, b: Box) {
  const iw = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const ih = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  if (iw <= 0 || ih <= 0) return 0;
  const smaller = Math.min(a.w * a.h, b.w * b.h);
  return smaller > 0 ? (iw * ih) / smaller : 0;
}

function bestSubjectMatch(subjects: Dict[] | undefined, faceBox: Box) {
  let subject: Dict | null = null;
  let index = -1;
  let best = 0.5;
  (Array.isArray(subjects) ? subjects : []).forEach((s, i) => {
    if (!finiteBox(s?.box)) return;
    const score = boxOverlapCoefficient(faceBox, s.box);
    if (score > best) {
      best = score;
      subject = s;
      index = i;
    }
  });
  return { subject, index };
}

function subjectForDetection(a: Analysis, detectionIndex: number, faceBox: Box) {
  const review = (a.local_face_review || []).find((r) => r?.detection_index === detectionIndex);
  if (review) {
    const idx = review.matched_subject_index;
    const subjects = Array.isArray(a.subjects) ? a.subjects : [];
    const subject = Number.isInteger(idx) && idx >= 0 && idx < subjects.length ? subjects[idx] : null;
    return { subject, index: subject ? (idx as number) : -1, matched: true };
  }
  return { ...bestSubjectMatch(a.subjects, faceBox), matched: false };
}

function issuesFor(subject: Dict, index: number, overrides: Record<string, FaceOverride>): FaceIssue[] {
  const out: FaceIssue[] = [];
  for (const c of FACE_CATEGORIES) {
    const match = subject[c.field];
    if (!match || typeof match !== "object") continue;
    const forced = !!match._user_forced;
    const disabled = !!match._user_disabled;
    const key = `${index}:${c.field}`;
    const issue: FaceIssue = {
      key,
      category: c.field,
      label: c.label,
      option: c.option,
      flagged: match[c.flagField] === true || disabled,
      allowed: (match[c.flagField] === true || forced) && !disabled,
      level: c.field === "hair_issue" ? match._user_level || "dynamic" : c.field === "smile_issue" ? match._user_level || "auto" : null,
      levelUserTouched: !!match._user_level,
    };
    const o = overrides[key];
    if (o === false) issue.allowed = false;
    else if (o && typeof o === "object") {
      issue.allowed = (o as Dict).allowed !== false;
      if (o.level) {
        issue.level = o.level;
        issue.levelUserTouched = true;
      }
    }
    out.push(issue);
  }
  return out;
}

function localFaceBoxes(a: Analysis): Dict[] {
  if (Array.isArray(a._local_face_boxes)) return a._local_face_boxes;
  if (Array.isArray(a._face_crop_boxes)) return a._face_crop_boxes.map((box) => ({ box, sent_to_model: true }));
  return [];
}

export function faceEntries(a: Analysis, overrides: Record<string, FaceOverride> = {}): FaceEntry[] {
  const out: FaceEntry[] = [];
  localFaceBoxes(a).forEach((f, i) => {
    if (!finiteBox(f?.box)) return;
    const sentToModel = f.sent_to_model !== false;
    const { subject, index, matched } = subjectForDetection(a, i + 1, f.box);
    if (!subject && (matched || !sentToModel)) return;
    out.push({
      box: f.box,
      subjectIndex: index,
      label: (subject as Dict | null)?.label || "",
      issues: subject ? issuesFor(subject, index, overrides) : [],
    });
  });
  return out;
}

// The plan's face_issue_overrides for the entries as the user left them.
export function overridesFromEntries(entries: FaceEntry[]): Record<string, FaceOverride> {
  const out: Record<string, FaceOverride> = {};
  for (const e of entries) {
    for (const issue of e.issues) {
      if (issue.allowed === false) out[issue.key] = false;
      else if (issue.level && issue.levelUserTouched) out[issue.key] = { allowed: true, level: issue.level };
      else if (issue.allowed) out[issue.key] = { allowed: true };
    }
  }
  return out;
}

export function cropFaceThumbnail(img: HTMLImageElement, box: Box): string {
  const nw = img.naturalWidth;
  const nh = img.naturalHeight;
  if (!nw || !nh) return "";
  const sx = Math.max(0, Math.round(box.x * nw));
  const sy = Math.max(0, Math.round(box.y * nh));
  const sw = Math.min(nw - sx, Math.max(1, Math.round(box.w * nw)));
  const sh = Math.min(nh - sy, Math.max(1, Math.round(box.h * nh)));
  const canvas = document.createElement("canvas");
  canvas.width = sw;
  canvas.height = sh;
  try {
    canvas.getContext("2d")!.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
    return canvas.toDataURL("image/jpeg", 0.85);
  } catch {
    return "";
  }
}

// The people the panel's face tabs show, named as the tabs name them. The
// backend's chat_brief.face_people numbers them the same way.
export interface Person {
  subjectIndex: number;
  name: string;
  box: Box;
}

export function facePeople(a: Analysis | null): Person[] {
  if (!a) return [];
  return faceEntries(a).map((f, i) => ({ subjectIndex: f.subjectIndex, name: f.label || `人物 ${i + 1}`, box: f.box }));
}

// Face crops of the source image, one per facePeople entry (same order).
const thumbCache = new Map<string, string[]>();

export function useFaceThumbs(a: Analysis | null, sourceUrl: string): string[] {
  return useBoxThumbs(facePeople(a).map((p) => p.box), sourceUrl);
}

// The same crops for boxes that did not come from an analysis -- the upload
// page's, which the local detector found before any session existed.
export function useBoxThumbs(boxes: Box[], sourceUrl: string): string[] {
  const key = `${sourceUrl}#${boxes.map((b) => [b.x, b.y, b.w, b.h].join(",")).join(";")}`;
  // The crops remember which photo and boxes they were cut for. Without that,
  // switching to a photo with no face kept the previous photo's crops around
  // (nothing to crop, so nothing ever replaced them), and a caller waiting on
  // crops == boxes waited forever.
  const [state, setState] = useState<{ key: string; thumbs: string[] }>({ key, thumbs: thumbCache.get(key) || [] });
  useEffect(() => {
    const cached = thumbCache.get(key);
    if (cached) {
      setState({ key, thumbs: cached });
      return;
    }
    if (!boxes.length) return;
    let alive = true;
    const img = new Image();
    const done = (out: string[]) => {
      thumbCache.set(key, out);
      if (alive) setState({ key, thumbs: out });
    };
    img.onload = () => done(boxes.map((b) => cropFaceThumbnail(img, b)));
    // One crop per box either way: a caller waiting for the picker to be ready
    // waits on the count, and an image that will not load must not be a wait
    // with no end.
    img.onerror = () => done(boxes.map(() => ""));
    img.src = sourceUrl;
    return () => {
      alive = false;
    };
  }, [key]);
  if (!boxes.length) return [];
  return state.key === key ? state.thumbs : thumbCache.get(key) || [];
}

// ── Before there is a session ───────────────────────────────────────────────
// The upload page runs the same local detector on the photo the moment it is
// picked, so 指定對象 is there while the user is still typing the first
// request. There are no subjects yet -- nothing has been analyzed -- so these
// people have no names, and subjectIndex is just their place in the detector's
// own order, which is the order the analysis will list them in later
// (image_tools.detect_face_boxes).
const detectCache = new Map<string, Box[]>();

// `ready` is false only while the detector is still out. No faces found and
// still looking both read as an empty picker, and the upload page waits on this
// to tell them apart: it holds the conversation until the people in the photo
// are on screen to point at.
export function useDetectedFaces(
  key: string | null,
  load: () => Promise<{ blob: Blob; name: string }>,
): { people: Person[]; ready: boolean } {
  const [boxes, setBoxes] = useState<Box[]>(() => (key && detectCache.get(key)) || []);
  const [ready, setReady] = useState(() => !key || detectCache.has(key));
  useEffect(() => {
    if (!key) {
      setBoxes([]);
      setReady(true);
      return;
    }
    const cached = detectCache.get(key);
    if (cached) {
      setBoxes(cached);
      setReady(true);
      return;
    }
    let alive = true;
    setBoxes([]);
    setReady(false);
    (async () => {
      let out: Box[] = [];
      try {
        const { blob, name } = await load();
        const res = await api.detectFaces(blob, name);
        out = (Array.isArray(res.faces) ? res.faces : []).map((f) => f?.box).filter(finiteBox);
      } catch {
        // Best-effort, exactly as the endpoint is: plenty of photos have no
        // face, and a photo the detector choked on should still be usable.
        // Either way the page simply shows no picker.
      }
      detectCache.set(key, out);
      if (!alive) return;
      setBoxes(out);
      setReady(true);
    })();
    return () => {
      alive = false;
    };
  }, [key]);
  // No name, because there is nobody named yet: 人物 2 would be this page's own
  // count dressed up as a name, and the face is right there on the chip -- the
  // crop is who this is. Where a word is unavoidable (a title, a screen
  // reader), the caller puts the position into words instead.
  return { people: boxes.map((box, i) => ({ subjectIndex: i, name: "", box })), ready };
}
