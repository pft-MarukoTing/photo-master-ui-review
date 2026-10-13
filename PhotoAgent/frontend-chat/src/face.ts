// Port of the old review card's portrait-check model (results.js
// faceIssueEntries / subjectForDetection / getFaceIssueOverrides), minus body
// reshape, which stays off in Stage 1.
import { useEffect, useState } from "preact/hooks";
import { api } from "./api";
import type { Analysis, Dict, FaceOverride } from "./types";
import { executedPrompt } from "./util";

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
  thumbnailBox?: Box;
  detectionIndex?: number; // 1-based detector ID, absent for AI-only subjects
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

// A subject box is a person thumbnail, never a fabricated detector result.
function recoverableSubject(a: Analysis, subject: Dict): boolean {
  const box = subject?.box;
  if (subject?.is_person !== true || !finiteBox(box)
    || box.x < 0 || box.y < 0 || box.w <= 0 || box.h <= 0
    || box.x + box.w > 1 || box.y + box.h > 1) return false;
  const detection = subject.local_detection_index;
  return !(Number.isInteger(detection) && detection > 0
    && (a.local_face_review || []).some(r => r?.detection_index === detection
      && (r.matched_subject_index === -1 || r.is_real_face === false)));
}

export function faceEntries(a: Analysis, overrides: Record<string, FaceOverride> = {}): FaceEntry[] {
  const out: FaceEntry[] = [];
  if (Array.isArray(a._fixed_preview_person_ids)) {
    const ids = new Set(a._fixed_preview_person_ids);
    return (a.subjects || []).flatMap((subject, index) => {
      if (!ids.has(subject.preview_person_id) || !finiteBox(subject.box)) return [];
      const thumbnail = a._subject_thumbnail_boxes?.[String(index)];
      return [{ box: subject.box, thumbnailBox: finiteBox(thumbnail) ? thumbnail : subject.box,
        subjectIndex: index, label: subject.label || "",
        issues: subject.is_person === true ? issuesFor(subject, index, overrides) : [] }];
    });
  }
  const represented = new Set<number>();
  localFaceBoxes(a).forEach((f, i) => {
    if (!finiteBox(f?.box)) return;
    const sentToModel = f.sent_to_model !== false;
    const { subject, index, matched } = subjectForDetection(a, i + 1, f.box);
    if (!subject && (matched || !sentToModel)) return;
    if (index >= 0) {
      if (represented.has(index)) return;
      represented.add(index);
    }
    out.push({
      detectionIndex: i + 1,
      box: f.box,
      subjectIndex: index,
      label: (subject as Dict | null)?.label || "",
      issues: subject ? issuesFor(subject, index, overrides) : [],
    });
  });
  (Array.isArray(a.subjects) ? a.subjects : []).forEach((subject, index) => {
    if (represented.has(index) || !recoverableSubject(a, subject)) return;
    out.push({ box: subject.box, subjectIndex: index, label: subject.label || "",
      issues: issuesFor(subject, index, overrides) });
  });
  return out;
}

// ── What a finished version actually did to each person ─────────────────────
// Port of the old result row's 人像調整 read-out (results.js
// buildPortraitInspectionEntries). Same rule as the other result tags
// (util.ts executedPrompt): a fix counts only when it was allowed for that
// person AND its execution block is in the Editor prompt -- inside that
// person's own `Subject:` section when there is more than one, so a fix for one
// person never lands on everyone. Labels are this UI's own (FACE_CATEGORIES,
// plus 身形調整 as the assistant names it), not the old row's 閉眼偵測 etc.
const EXECUTED_FACE_TAGS = [
  {
    field: "blink_issue",
    option: "eye_closed_detection",
    label: "閉眼",
    markers: [
      /Accidental blink repair priority:/i,
      /PRIMARY EYE-PAIR REPAIR/i,
      /\beyelid correction\b/i,
      /^\s*-\s*Eye-pair repair\s*\([^\n]*\):\s*(?:open|use)\b/im,
    ],
  },
  {
    field: "gaze_issue",
    option: "gaze_contact_detection",
    label: "視線",
    markers: [
      /Approved gaze repair execution instructions:/i,
      /ONLY task for this edit:[\s\S]{0,180}redirect[^\n]*gaze/i,
      /Use a .* eyes-(?:only|plus-head\/face) correction[\s\S]{0,240}direct lens contact/i,
      /Required change:[\s\S]{0,900}(?:direct(?:ly)?|camera lens)[^\n]*/i,
      /^\s*-\s*Use a [^\n]*eyes-(?:only|plus-head\/face) correction[^\n]*;\s*reach clear direct lens contact\./im,
    ],
  },
  {
    field: "hair_issue",
    option: "hair_mess_detection",
    label: "髮絲",
    markers: [
      /Hair tidying priority:/i,
      /Still-air hair \(user-selected, mandatory\):/i,
      /Hair level — (?:local cleanup|fully groomed|preserve motion)/i,
      /Hair (?:correction plan|target) —?/i,
    ],
  },
  {
    field: "smile_issue",
    option: "smile_adjustment",
    label: "笑容",
    markers: [
      /Smile adjustment \(user-selected, mandatory\)/i,
      /target_smile:/i,
      /(?:Transform this person's existing|Render) .*? smile on the existing face/i,
    ],
  },
];

const FLAG_FIELD: Record<string, string> = {
  blink_issue: "should_fix",
  gaze_issue: "should_fix",
  hair_issue: "should_fix",
  smile_issue: "adjust_safe",
};

const EXECUTED_BODY_MARKER = /Body reshape execution instructions \(analysis-approved\):/i;

const escapeRegExp = (v: string) => v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// The Editor prompt groups commands under `Subject: <label>` (the gaze-only
// pass with a leading dash); this is that person's block, or "" if absent.
function subjectSection(prompt: string, subject: Dict): string {
  const label = String(subject?.label || "").trim();
  if (!label) return "";
  const heading = new RegExp(`(?:^|\\n)[ \\t]*(?:-\\s*)?Subject:[ \\t]*${escapeRegExp(label)}[ \\t]*(?:\\n|$)`, "i");
  const match = heading.exec(prompt);
  if (!match) return "";
  const restStart = match.index + match[0].length;
  const end = prompt
    .slice(restStart)
    .search(
      /\n[ \t]*(?:-\s*)?Subject:[ \t]*|\n[ \t]*(?:Shared facial execution rules|Body reshape execution instructions|Targets|Other approved corrections|Finished light and color target|Scope is limited|Execution by method):/i,
    );
  return prompt.slice(match.index, end >= 0 ? restStart + end : prompt.length);
}

function hasSubjectMarker(prompt: string, subject: Dict, markers: RegExp[], allowGeneric: boolean, count: number) {
  const scoped = subjectSection(prompt, subject) || (count <= 1 ? prompt : "");
  if (markers.some((m) => m.test(scoped))) return true;
  return allowGeneric && count <= 1 && /For every person whose expression is readable:\s*target_smile:/i.test(prompt);
}

export interface PortraitDone {
  label: string;
  // The detector's crop of this person, for the row's thumbnail.
  box: Box;
  tags: string[];
}

export function executedPortraits(a: Analysis | null | undefined): PortraitDone[] {
  const prompt = executedPrompt(a);
  if (!a || !prompt) return [];
  const advanced = (a._advanced_options || {}) as Dict;
  const bodyEnabled = a._body_reshape_enabled !== false;
  const disabledBodies = new Set(Array.isArray(a._disabled_body_reshape_subjects) ? a._disabled_body_reshape_subjects : []);
  const subjects = Array.isArray(a.subjects) ? a.subjects : [];

  // Every person once, using the same recovery eligibility as the face panel.
  const seen = new Set<number>();
  const people: { subject: Dict; index: number; box: Box | null }[] = [];
  localFaceBoxes(a).forEach((f, i) => {
    const box = finiteBox(f?.box) ? f.box : finiteBox(f?.face_box) ? f.face_box : null;
    if (!box) return;
    const { subject, index } = subjectForDetection(a, i + 1, box);
    if (!subject || (subject as Dict).is_person !== true || index < 0 || seen.has(index)) return;
    seen.add(index);
    people.push({ subject, index, box });
  });
  subjects.forEach((s, i) => {
    if (seen.has(i) || !recoverableSubject(a, s)) return;
    seen.add(i);
    people.push({ subject: s, index: i, box: finiteBox(a._subject_thumbnail_boxes?.[String(i)]) ? a._subject_thumbnail_boxes[String(i)] : s.box });
  });

  return people
    .map(({ subject, index, box }) => {
      const tags: string[] = [];
      for (const def of EXECUTED_FACE_TAGS) {
        const match = subject[def.field];
        if (!match || typeof match !== "object" || advanced[def.option] === false) continue;
        const allowed = (match[FLAG_FIELD[def.field]] === true || !!match._user_forced) && !match._user_disabled;
        if (allowed && hasSubjectMarker(prompt, subject, def.markers, def.field === "smile_issue", people.length)) {
          tags.push(def.label);
        }
      }
      const body = subject.body_reshape;
      const bodyPrompt = typeof body?.edit_prompt === "string" ? body.edit_prompt.trim() : "";
      const name = String(subject.label || "").trim();
      // Prefer the exact approved body sentence: every active subject is named
      // in the body block, so a name-only check would tick a second person.
      const targeted = bodyPrompt ? prompt.includes(bodyPrompt) : name ? prompt.includes(name) : true;
      if (
        bodyEnabled &&
        body &&
        typeof body === "object" &&
        body.should_edit !== false &&
        !body._user_disabled &&
        body.analysis_status !== "error" &&
        !disabledBodies.has(index) &&
        bodyPrompt &&
        EXECUTED_BODY_MARKER.test(prompt) &&
        targeted
      ) {
        tags.push("身形調整");
      }
      return { label: name || `人物 ${index + 1}`, box, tags: [...new Set(tags)] };
    })
    .filter((p): p is PortraitDone => !!p.box && p.tags.length > 0);
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
  thumbnailBox?: Box;
  detectionIndex?: number;
  subjectIndex: number;
  name: string;
  box: Box;
}

export function facePeople(a: Analysis | null): Person[] {
  if (!a) return [];
  return faceEntries(a).map((f, i) => ({ detectionIndex: f.detectionIndex, subjectIndex: f.subjectIndex, name: f.label || `人物 ${i + 1}`, box: f.box, thumbnailBox: f.thumbnailBox }));
}

// Face crops of the source image, one per facePeople entry (same order).
const thumbCache = new Map<string, string[]>();

export function useFaceThumbs(a: Analysis | null, sourceUrl: string): string[] {
  return useBoxThumbs(facePeople(a).map((p) => p.thumbnailBox || p.box), sourceUrl);
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
