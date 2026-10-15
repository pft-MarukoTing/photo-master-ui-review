// The adjustment options the chat offers, one category per card: the portrait
// fixes person by person, the analyzer's retouch recipe, the style groups, and
// the extra instruction (個人化需求) once there is one, and the render settings
// (設定) while adjusting or once a typed request changed them.
// Every click is saved to the photo's plan right away as a patch.
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { urls } from "./api";
import { fillNoteBox, noteBoxKeys, promptFaces, promptSections, readPromptBox, type FaceRef } from "./chat";
import { FACE_CATEGORIES, faceEntries, LEVEL_OPTIONS, POSE_FACE_RULES, poseFacePatch, type FaceEntry, type FaceIssue, type PoseFaceResult } from "./face";
import { Icon } from "./icons";
import type { Analysis, Capabilities, FaceOverride, Plan } from "./types";
import { aspectLabel, FIXED_RATIO_RE, recipeItems, REFRAME_RE, renderOptionLabel } from "./util";

export type Category = "face" | "recipe" | "style" | "prompt" | "settings";

// term：放進句子或接在動詞後面時的叫法。「畫面」單獨當按鈕很清楚，但「看畫面」
// 「這是更新後的畫面」會被讀成在講照片本身，所以句子裡說「畫面建議」。
export const CATEGORIES: Record<Category, { label: string; icon: string; intro: string; term?: string }> = {
  face: { label: "人像", icon: "person", intro: "有小圓點的是看過原圖後建議調整的項目，點一下可以開或關。" },
  recipe: {
    label: "畫面",
    term: "畫面建議",
    icon: "recipe",
    intro: "這些是看過原圖後，針對光線、色調、構圖這些整張畫面的建議，打勾的會套用，點一下可以開或關。",
  },
  style: { label: "風格", icon: "wand", intro: "想換一種氛圍的話，先挑想改的類別再選風格；不選就維持照片原本的樣子。" },
  prompt: { label: "個人化需求", icon: "note", intro: "這段個人化需求會在美化時一起套用，可以直接修改或清除。" },
  settings: { label: "設定", icon: "tune", intro: "這張照片美化時用的引擎與輸出設定，可以直接修改。" },
};

// 美化過一版之後再打開面板，看到的已經不是剛分析完的預設，而是上一版的設定。
// 開場白要把這件事講出來：建議還是看原圖給的，開著關著的則是上次留下來的。
const AGAIN_INTRO: Record<Category, string> = {
  face: "開或關沿用上次美化的設定，有小圓點的是看過原圖後建議調整的項目，點一下可以改。",
  recipe: "這些是看原圖分析出來、整張畫面的建議，打勾的狀態沿用上次美化，點一下可以開或關。",
  style: "這是上次選的風格，想換可以直接改；不選就維持照片原本的樣子。",
  prompt: "這是上次的個人化需求，可以直接修改或清除。",
  settings: "這是上次美化用的引擎與輸出設定，可以直接修改。",
};

export const PROMPT_MAX = 2000; // chat_plan.USER_PROMPT_MAX_LEN

const SUGGESTED_HINT = "看過原圖後建議調整";

// 這個人有沒有東西要修。分析標出來的（flagged）和已經開著的（allowed）都算 --
// 用於修正摘要；有分析項目但未建議修正的人仍可在面板自行設定。
const hasFixes = (f: FaceEntry) => f.subjectIndex >= 0 && f.issues.some((x) => x.flagged || x.allowed);
const canConfigurePerson = (f: FaceEntry) => f.subjectIndex >= 0 && f.issues.length > 0;

// 面板的開場白。again：這一輪是美化過之後的再調整，見 AGAIN_INTRO。人像只有在
// 面板裡真的列出不只一個人時，才講「每個人可以分開調整」-- 只有一個人的照片講
// 這句，就是在說一件看不到的事。
export function introFor(cat: Category, a: Analysis, plan: Plan, again = false): string {
  const base = again ? AGAIN_INTRO[cat] : CATEGORIES[cat].intro;
  if (cat !== "face") return base;
  const people = faceEntries(a, plan.face_issue_overrides || {}).filter(canConfigurePerson).length;
  return people > 1 ? `照片裡每個人都可以分開調整，${base}` : base;
}

export const termOf = (cat: Category) => CATEGORIES[cat].term || CATEGORIES[cat].label;

// The categories this photo has anything to show in, in the order offered.
export function categoriesFor(a: Analysis, caps: Capabilities, plan: Plan): Category[] {
  const out: Category[] = [];
  if (faceEntries(a, plan.face_issue_overrides || {}).some(canConfigurePerson)) out.push("face");
  if (recipeItems(a).length) out.push("recipe");
  if (styleGroups(a, caps).length) out.push("style");
  if (plan.user_prompt?.trim()) out.push("prompt");
  return out;
}

// 分析完成後，助理一次只談一個類別。這句是那一步的開場：說我們在這張照片上
// 看到什麼、會怎麼處理，再問要不要看。能點名就點名真的偵測到的東西 --「人像有
// 幾項要修」任何一張照片都講得出來，就等於沒看過這一張。只有名稱本身長到會蓋掉
// 這句話的（畫面建議），才退回用項目數帶過。
const short = (s: string, n = 14) => (s.length > n ? `${s.slice(0, n)}…` : s);

export function topicLead(cat: Category, a: Analysis, caps: Capabilities, plan: Plan): string {
  switch (cat) {
    case "face": {
      // 先 map 再 filter：人物 N 的編號要跟面板的分頁一致，而面板是用完整
      // faceEntries 的順序編的。
      const who = faceEntries(a)
        .map((f, i) => ({
          name: f.label || `人物 ${i + 1}`,
          fixes: f.subjectIndex >= 0 ? f.issues.filter((x) => x.flagged).map((x) => x.label) : [],
        }))
        .filter((p) => p.fixes.length);
      if (!who.length) return "人像我幫你看過了，有幾項可以自己決定要不要調整。要先看一下嗎？";
      // 三個人以上就只點名字，不然一句話要塞六七個項目，反而看不出重點。
      if (who.length > 2)
        return `人像我幫你看過了，${who.slice(0, 2).map((p) => p.name).join("、")}等 ${who.length} 個人都有要調整的地方。要先看一下嗎？`;
      const named = who.map((p) => `${p.name}的${p.fixes.join("、")}`).join("、");
      return `人像我幫你看過了，${named}會幫你調整。要先看一下嗎？`;
    }
    case "recipe":
      // 不點名：這裡的每一項本身就是一整句話，念兩項就把這句開場撐成一段，反而
      // 看不出在問什麼。項目數先帶過，細節等他點開面板再看。
      return `畫面的部分（光線、色調、構圖這些），我有 ${recipeItems(a).length} 項建議，預設都會套用。要看一下嗎？`;
    case "style": {
      const names = styleGroups(a, caps).slice(0, 3).map((g) => g.label).join("、");
      return `最後是風格，${names}可以換一種氛圍，不選就維持照片原本的樣子。要看看嗎？`;
    }
    case "prompt":
      return `你先前寫的「${short(plan.user_prompt?.trim() || "", 18)}」美化時會一起套用。要看一下嗎？`;
    default:
      return "最後是設定，可以改美化時用的引擎和輸出尺寸。要看一下嗎？";
  }
}

// 面板收起後留在對話裡的那一句：這一段最後談成什麼。plan 由呼叫端決定 --
// 傳進來的是那張卡收起時的快照，所以這句講的是當時的事，後面再改也不會變。
export function topicRecap(cat: Category, a: Analysis, caps: Capabilities, plan: Plan): string {
  switch (cat) {
    case "face": {
      const on = faceEntries(a, plan.face_issue_overrides || {})
        .map((f, i) => ({
          name: f.label || `人物 ${i + 1}`,
          items: f.issues.filter((x) => x.allowed).map((x) => x.label),
        }))
        .filter((e) => e.items.length);
      if (!on.length) return "人像目前都不修。";
      if (on.length > 2) return `人像會修 ${on.length} 個人。`;
      return `人像會修${on.map((e) => `${e.name}的${e.items.join("、")}`).join("、")}。`;
    }
    case "recipe": {
      // 只給數字：項目名稱本身都是一整句，列出來會把這一行撐成一段；要看細節
      // 的路就在同一則訊息的「再看畫面建議」。
      const n = recipeItems(a).filter((_, i) => (plan.recipe_checked || [])[i] !== false).length;
      return n ? `畫面建議會套用 ${n} 項。` : "畫面建議目前都不套用。";
    }
    case "style": {
      const picked = styleGroups(a, caps)
        .map((g) => {
          const v = plan.style_options?.[g.id] || g.default;
          return v === g.default ? "" : `${g.label}：${g.choices.find((c) => c.value === v)?.label || v}`;
        })
        .filter(Boolean);
      return picked.length ? `風格選了${picked.join("、")}。` : "風格維持照片原本的樣子。";
    }
    case "prompt":
      return plan.user_prompt?.trim() ? `個人化需求：「${short(plan.user_prompt.trim(), 24)}」` : "";
    default:
      return "";
  }
}

// The category a plan field (a proposal change's `field`) is shown in.
export function categoryOf(field: string): Category | null {
  if (field.startsWith("face_issue_overrides.")) return "face";
  if (field.startsWith("recipe_checked.")) return "recipe";
  if (field.startsWith("style_options.")) return "style";
  if (field === "user_prompt") return "prompt";
  if (["preset", "render_option", "aspect_ratio", "run_count"].includes(field) || field.startsWith("advanced_options.")) return "settings";
  return null;
}

// Fields to flash, set again (new nonce) each time the chat points at them.
export interface Hot {
  fields: string[];
  nonce: number;
  // Scroll the card into view. Off when it just opened at the end of the
  // timeline: the panel already sticks there, and scrolling would take the
  // talk back up.
  reveal?: boolean;
}

const HOT_MS = 2400;

// Bring the card into view inside the chat timeline only. scrollIntoView is no
// good here: it scrolls the page behind the panel as well, and on a card taller
// than the timeline it aligns the top, jumping away from the end of the talk.
function reveal(el: HTMLElement | null) {
  const box = el?.closest(".chat-timeline") as HTMLElement | null;
  if (!el || !box) return;
  const card = el.getBoundingClientRect();
  const view = box.getBoundingClientRect();
  const pad = 12;
  let delta = 0;
  if (card.top < view.top + pad) delta = card.top - view.top - pad;
  else if (card.bottom > view.bottom - pad)
    delta = Math.min(card.bottom - view.bottom + pad, card.top - view.top - pad);
  if (delta) box.scrollBy({ top: delta, behavior: "smooth" });
}

function styleGroups(a: Analysis, caps: Capabilities) {
  return caps.style_options.filter((g) => g.id !== "pose" || a.subject_category === "人物");
}

interface Props {
  category: Category;
  analysis: Analysis;
  plan: Plan;
  caps: Capabilities;
  faces: Record<number, FaceRef>;
  busy: boolean;
  hot: Hot | null;
  onPatch: (patch: Plan) => void;
  // 個人化需求存檔，存完請 AI 再看一次內容。
  onSavePrompt: (text: string) => void;
}

export function OptionsCard(props: Props) {
  const [hot, setHot] = useState<Set<string>>(new Set());
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!props.hot) return;
    setHot(new Set(props.hot.fields));
    if (props.hot.reveal) reveal(box.current);
    const t = window.setTimeout(() => setHot(new Set()), HOT_MS);
    return () => clearTimeout(t);
  }, [props.hot?.nonce]);

  const Body = { face: FaceOptions, recipe: RecipeOptions, style: StyleOptions, prompt: PromptOptions, settings: SettingsOptions }[props.category];
  const body = <Body {...props} hot={hot} />;
  return (
    <div class="chat-card chat-options" ref={box}>
      {body}
    </div>
  );
}

type Body = Omit<Props, "hot"> & { hot: Set<string> };

// 「看過原圖後建議調整」是註記，不是第二個標籤 -- 一顆小圓點就夠，開場白會說它的
// 意思。點本身沒有字，所以 aria-label 要把那句話補回去給螢幕閱讀器。
function OptChip(props: { label: string; on: boolean; hot?: boolean; suggested?: boolean; disabled?: boolean; title?: string; onClick: () => void }) {
  return (
    <button
      type="button"
      class={`chat-chip chat-opt-chip${props.on ? " is-picked" : ""}${props.hot ? " is-ai-hot" : ""}`}
      disabled={props.disabled}
      title={props.title || (props.suggested ? SUGGESTED_HINT : undefined)}
      onClick={props.onClick}
    >
      {props.on && <Icon name="check" size={12} />}
      {props.label}
      {props.suggested && <span class="chat-opt-dot" role="img" aria-label={SUGGESTED_HINT} />}
    </button>
  );
}

// 髮絲跟笑容挑得了程度，程度就寫在按鈕上：一顆藥丸切兩半，左半邊照常開關，右
// 半邊的箭頭掀開一張程度的選單。不另外再給一列寫著同一個名字的摺疊 -- 同一件
// 事說兩次，兩次都要讀。
function LevelChip(props: {
  label: string;
  levels: { value: string; label: string }[];
  value: string;
  on: boolean;
  open: boolean;
  hot?: boolean;
  suggested?: boolean;
  disabled?: boolean;
  onToggle: () => void;
  onOpen: () => void;
  onClose: () => void;
  onPick: (value: string) => void;
}) {
  const { open, onClose } = props;
  const wrap = useRef<HTMLSpanElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const cur = props.levels.find((o) => o.value === props.value) || props.levels[0];
  // 選單預設往下掀。這張卡就在對話流裡，常常是最後一則，往下掀會被訊息區的下緣
  // 切掉 -- 下面裝不下、上面裝得下就改成往上。
  const [up, setUp] = useState(false);
  useLayoutEffect(() => {
    if (!open || !wrap.current || !menu.current) return;
    const r = wrap.current.getBoundingClientRect();
    const h = menu.current.offsetHeight + 8;
    const box = wrap.current.closest(".chat-timeline")?.getBoundingClientRect();
    const top = box ? box.top : 0;
    const bottom = box ? box.bottom : window.innerHeight;
    setUp(r.bottom + h > bottom && r.top - h > top);
  }, [open]);
  // 點到別處、按 Esc 就收 -- 選單蓋在對話上面，沒收起來會擋住下一句話。
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) onClose();
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open, onClose]);
  return (
    <span class={`chat-opt-split${props.on ? " is-split" : ""}${props.hot ? " is-ai-hot" : ""}`} ref={wrap}>
      <button
        type="button"
        class={`chat-chip chat-opt-chip${props.on ? " is-picked" : ""}`}
        disabled={props.disabled}
        title={props.suggested ? SUGGESTED_HINT : undefined}
        onClick={props.onToggle}
      >
        {props.on && <Icon name="check" size={12} />}
        {props.on ? `${props.label}：${cur.label}` : props.label}
        {props.suggested && <span class="chat-opt-dot" role="img" aria-label={SUGGESTED_HINT} />}
      </button>
      {props.on && (
        <button
          type="button"
          class="chat-opt-split-more"
          disabled={props.disabled}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={`換${props.label}的程度`}
          title={`換${props.label}的程度`}
          onClick={props.onOpen}
        >
          <span class={`chat-opt-caret${open ? " is-open" : ""}`} />
        </button>
      )}
      {open && (
        <div class={`chat-opt-menu${up ? " is-up" : ""}`} role="menu" ref={menu}>
          {props.levels.map((o) => (
            <button
              type="button"
              role="menuitemradio"
              aria-checked={o.value === cur.value}
              class={o.value === cur.value ? "is-picked" : undefined}
              onClick={() => props.onPick(o.value)}
            >
              <span class="chat-opt-menu-tick">{o.value === cur.value && <Icon name="check" size={14} />}</span>
              {o.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

// 一張卡裡攤開幾個人還讀得完的上限。再多就先只給人頭，點了才看那個人的項目 --
// 這張卡夾在對話中間，高度是跟對話借的。
const FOLD_PEOPLE_OVER = 3;
// hot 欄位 face_issue_overrides.<subjectIndex>:<category> 裡的那個人。
const HOT_PERSON_RE = /^face_issue_overrides\.(\d+):/;

function FaceOptions({ analysis, plan, faces, busy, hot, onPatch }: Body) {
  // 正在挑程度的那一項（issue.key），一次一項：選單是浮起來的，同時開兩張會疊
  // 在一起。挑完就收起來 -- 選了什麼按鈕上就寫著。
  const [openLevel, setOpenLevel] = useState<string | null>(null);
  // 人多的時候展開中的那個人（subjectIndex），一次一個：每一次點都已經存進
  // plan，收起來不會掉東西，列上那句話也說得完他會修什麼。
  const [openPerson, setOpenPerson] = useState<number | null>(null);
  // AI 動到的項目要看得到才算標記過，所以先把那個人展開。
  useEffect(() => {
    const m = Array.from(hot)
      .map((f) => f.match(HOT_PERSON_RE))
      .find(Boolean);
    if (m) setOpenPerson(Number(m[1]));
  }, [hot]);
  const locked = plan.advanced_options || {};
  // 人物 N 的編號跟著完整的 faceEntries 走，和面板分頁、開場白那句話同一套 --
  // 這裡少列幾個人，不該讓剩下的人改名。
  const all = faceEntries(analysis, plan.face_issue_overrides || {});
  const entries = all.filter(canConfigurePerson);
  const set = (issue: FaceIssue, value: FaceOverride) =>
    onPatch({ face_issue_overrides: { [issue.key]: value } });
  const faceOf = (f: FaceEntry): FaceRef => faces[f.subjectIndex] || { name: f.label || `人物 ${all.indexOf(f) + 1}`, thumb: "" };
  // 這張照片分析出來的原樣，key -> 狀態。拿來比對現在的設定，才知道哪些是使用
  // 者自己動過的 -- 列上那句話只說「現在會修什麼」，說不出那是建議還是他改的。
  const base = new Map<string, string>();
  for (const f of faceEntries(analysis, {})) for (const x of f.issues) base.set(x.key, `${x.allowed}:${x.level || ""}`);
  const changedOf = (f: FaceEntry) => f.issues.some((x) => base.get(x.key) !== `${x.allowed}:${x.level || ""}`);
  // 這個人目前會修什麼。「不修」跟面板收起後那句結論用同一個說法。在「設定」
  // 整項關掉的不算，那一項本來就不會跑。
  const summaryOf = (f: FaceEntry) =>
    f.issues
      .filter((x) => x.allowed && locked[x.option] !== false)
      .map((x) => x.label)
      .join("、") || "不修";
  // 一個人的項目。收起來跟攤開來是同一份內容，差別只在外面那一層。
  const issuesOf = (f: FaceEntry) => (
    <div class="chat-choices">
      {f.issues.map((issue) => {
        const off = locked[issue.option] === false;
        const toggle = () =>
          set(issue, issue.allowed ? false : issue.levelUserTouched && issue.level ? { allowed: true, level: issue.level } : { allowed: true });
        const levels = LEVEL_OPTIONS[issue.category];
        if (levels && !off)
          return (
            <LevelChip
              label={issue.label}
              levels={levels}
              value={issue.level || levels[0].value}
              on={issue.allowed}
              open={openLevel === issue.key}
              suggested={issue.flagged}
              hot={hot.has(`face_issue_overrides.${issue.key}`)}
              disabled={busy}
              onToggle={toggle}
              onOpen={() => setOpenLevel(openLevel === issue.key ? null : issue.key)}
              onClose={() => setOpenLevel(null)}
              onPick={(value) => {
                set(issue, { allowed: true, level: value });
                setOpenLevel(null);
              }}
            />
          );
        return (
          <OptChip
            label={issue.label}
            on={!off && issue.allowed}
            suggested={issue.flagged}
            hot={hot.has(`face_issue_overrides.${issue.key}`)}
            disabled={busy || off}
            title={off ? "這一項已在「設定」的人像修正關閉" : undefined}
            onClick={toggle}
          />
        );
      })}
    </div>
  );

  // 人多的時候：先一排人頭，點了才展開那個人。
  if (entries.length > FOLD_PEOPLE_OVER)
    return (
      <div class="chat-opt-people">
        {entries.map((f) => {
          const face = faceOf(f);
          const open = openPerson === f.subjectIndex;
          return (
            <div class="chat-opt-person-fold">
              <button
                type="button"
                class={`chat-opt-person-head${open ? " is-open" : ""}`}
                aria-expanded={open}
                onClick={() => setOpenPerson(open ? null : f.subjectIndex)}
              >
                {face.thumb ? <img src={face.thumb} alt="" /> : <span class="chat-face-blank" />}
                <span class="chat-opt-person-name">{face.name}</span>
                <span class="chat-opt-person-sum">{summaryOf(f)}</span>
                {changedOf(f) && <span class="chat-opt-person-mark">已調整</span>}
                <span class={`chat-opt-caret${open ? " is-open" : ""}`} />
              </button>
              {open && <div class="chat-opt-person-body">{issuesOf(f)}</div>}
            </div>
          );
        })}
      </div>
    );
  return (
    <>
      {entries.map((f) => {
        const face = faceOf(f);
        return (
          <div class="chat-opt-person">
            <div class="chat-opt-person-label">
              {face.thumb ? <img src={face.thumb} alt="" /> : <span class="chat-face-blank" />}
              <span class="chat-opt-person-name">{face.name}</span>
            </div>
            {issuesOf(f)}
          </div>
        );
      })}
    </>
  );
}

function RecipeOptions({ analysis, plan, caps, busy, hot, onPatch }: Body) {
  const recipe = recipeItems(analysis);
  // 固定比例只能靠重新構圖做到：那一項一定勾著，要取消得從比例改。
  const locked = FIXED_RATIO_RE.test(plan.aspect_ratio || "");
  const checked = recipe.map((_, i) => (locked && REFRAME_RE.test(recipe[i])) || (plan.recipe_checked || [])[i] !== false);
  const order = recipe
    .map((text, i) => ({ text, i, reframe: REFRAME_RE.test(text) }))
    .sort((a, b) => Number(b.reframe) - Number(a.reframe));
  return (
    <ul class="chat-opt-list">
      {order.map(({ text, i, reframe }) => {
        const m = text.match(REFRAME_RE);
        const fixed = locked && reframe;
        return (
          <li>
            <button
              type="button"
              class={`chat-opt-row${checked[i] ? " is-on" : ""}${hot.has(`recipe_checked.${i}`) ? " is-ai-hot" : ""}`}
              disabled={busy}
              aria-disabled={fixed || undefined}
              onClick={() => !fixed && onPatch({ recipe_checked: checked.map((x, j) => (j === i ? !x : x)) })}
            >
              <span class="chat-opt-box">{checked[i] && <Icon name="check" size={12} />}</span>
              <span>
                {m ? (
                  <>
                    {`${m[1]} `}
                    <strong>{m[2]}</strong>
                  </>
                ) : (
                  text
                )}
              </span>
            </button>
            {fixed && (
              <p class="chat-note">
                選了 {plan.aspect_ratio} 就一定要重新構圖，想取消的話把比例改成「{aspectLabel(caps, "original")}」。
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}

// 姿勢自動改了誰的人像設定、或把前一個姿勢改的還原，要講出來 -- 不然人像那張卡上
// 的笑容、視線變了，使用者不知道是誰動的。
function poseNotice(caps: Capabilities, pose: string, r: PoseFaceResult): string[] {
  const choices = caps.style_options.find((g) => g.id === "pose")?.choices || [];
  const name = (v: string | null) => (v && choices.find((c) => c.value === v)?.label) || v || "";
  const label = (category: string) => FACE_CATEGORIES.find((c) => c.field === category)?.label || category;
  const rules = (POSE_FACE_RULES[pose] || []).filter((x) => r.applied.includes(x.category));
  if (!rules.length && !r.restored.length) return [];
  const title = !rules.length && r.previousPose
    ? pose === "default" ? `已取消「${name(r.previousPose)}」的自動調整` : `已切換為「${name(pose)}」`
    : `已配合「${name(pose)}」自動調整人像設定`;
  const lines = [title];
  r.restored.forEach((c) => lines.push(`${label(c)}：已還原為原本設定`));
  rules.forEach((x) => {
    const level = x.values.level && LEVEL_OPTIONS[x.category]?.find((o) => o.value === x.values.level)?.label;
    lines.push(`${label(x.category)}：${level || (x.values.allowed === false ? "關閉" : "開啟")}（${x.reason}）`);
  });
  return lines;
}

function StyleOptions({ analysis, plan, caps, busy, hot, onPatch }: Body) {
  const groups = styleGroups(analysis, caps);
  const [open, setOpen] = useState<string | null>(null);
  const [notice, setNotice] = useState<string[]>([]);
  // A group the chat points at opens by itself.
  useEffect(() => {
    const g = groups.find((x) => hot.has(`style_options.${x.id}`));
    if (g) setOpen(g.id);
  }, [hot]);
  const group = groups.find((g) => g.id === open);
  const valueOf = (g: (typeof groups)[number]) => plan.style_options?.[g.id] || g.default;
  // Picking closes the row -- the chip already shows what was picked, and an
  // open wall of thumbnails after the choice is made just pushes the chat down.
  // Clicking the picked thumbnail again only un-picks it and leaves the row
  // open: the person is mid-choice, not done.
  const pick = (value: string, close = true) => {
    if (!group) return;
    if (group.id === "pose") {
      const r = poseFacePatch(analysis, plan, value);
      onPatch({ style_options: { pose: value }, ...r.patch });
      setNotice(poseNotice(caps, value, r));
    } else onPatch({ style_options: { [group.id]: value } });
    if (close) setOpen(null);
  };
  return (
    <>
      <div class="chat-choices">
        {groups.map((g) => {
          const v = valueOf(g);
          const picked = v !== g.default ? g.choices.find((c) => c.value === v)?.label : "";
          return (
            <button
              type="button"
              class={`chat-chip chat-opt-chip${open === g.id ? " is-picked" : ""}${hot.has(`style_options.${g.id}`) ? " is-ai-hot" : ""}`}
              onClick={() => setOpen(open === g.id ? null : g.id)}
            >
              {g.label}
              {picked && <span class="chat-opt-value">：{picked}</span>}
              <span class={`chat-opt-caret${open === g.id ? " is-open" : ""}`} />
            </button>
          );
        })}
      </div>
      {group && (
        <div class="chat-opt-style-row">
          <button type="button" class={`chat-opt-style-none${valueOf(group) === group.default ? " is-picked" : ""}`} disabled={busy} onClick={() => pick(group.default)}>
            不指定
          </button>
          {group.choices
            .filter((c) => c.value !== group.default)
            .map((c) => (
              <button
                type="button"
                class={`chat-opt-style-btn${valueOf(group) === c.value ? " is-picked" : ""}`}
                disabled={busy}
                onClick={() => (valueOf(group) === c.value ? pick(group.default, false) : pick(c.value))}
              >
                <img src={urls.styleThumb(group.id, c.value)} alt="" loading="lazy" />
                <span>{c.label}</span>
              </button>
            ))}
        </div>
      )}
      {notice.length > 0 && (
        <p class="chat-recap">
          <strong>{notice[0]}</strong>
          {notice.slice(1).map((line) => (
            <>
              <br />
              {line}
            </>
          ))}
        </p>
      )}
    </>
  );
}

function PromptOptions({ plan, faces, busy, hot, onPatch, onSavePrompt }: Body) {
  const saved = plan.user_prompt || "";
  const sections = promptSections(saved, faces);
  // 已經寫過的需求先用看的，要改再打開輸入框 -- 一進來就停在一個沒改就按不下「儲存」
  // 的框裡，等於只剩「清除」這條路可以按，人會卡在那裡不知道該做什麼。
  const [editing, setEditing] = useState(!saved);
  const [dirty, setDirty] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setEditing(!saved);
    setDirty(false);
  }, [saved]);
  // 框裡的內容只在打開時建一次，之後交給使用者打字，Preact 不碰。還沒寫過東西時
  // 框是自己開著的，不是人按了「修改」，不搶游標。
  useEffect(() => {
    if (editing && box.current) fillNoteBox(box.current, sections, !!saved);
  }, [editing]);
  const save = () => {
    const text = box.current ? readPromptBox(box.current, sections).slice(0, PROMPT_MAX) : "";
    // 清空等於清除，沒有東西好給 AI 看。
    if (text === saved) setEditing(false);
    else if (text) onSavePrompt(text);
    else onPatch({ user_prompt: "" });
  };
  return (
    <div class={`chat-opt-prompt${hot.has("user_prompt") ? " is-ai-hot" : ""}`}>
      {editing ? (
        <div
          class="chat-notes-box"
          ref={box}
          role="textbox"
          aria-multiline="true"
          aria-label="個人化需求"
          data-placeholder="例如：讓背景更乾淨一點"
          contentEditable={!busy}
          onInput={() => !dirty && setDirty(true)}
          {...noteBoxKeys}
        />
      ) : (
        <p class="chat-opt-prompt-text">{promptFaces(sections)}</p>
      )}
      <div class="chat-choices">
        {editing ? (
          <>
            <button type="button" class="chat-chip chat-opt-chip is-primary" disabled={busy || !dirty} onClick={save}>
              儲存
            </button>
            {!!saved && (
              <button type="button" class="chat-chip chat-opt-chip is-ghost" disabled={busy} onClick={() => { setDirty(false); setEditing(false); }}>
                取消
              </button>
            )}
          </>
        ) : (
          <>
            <button type="button" class="chat-chip chat-opt-chip is-primary" disabled={busy} onClick={() => setEditing(true)}>
              修改
            </button>
            <button type="button" class="chat-chip chat-opt-chip is-ghost" disabled={busy} onClick={() => onPatch({ user_prompt: "" })}>
              清除
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function SettingsOptions({ plan, caps, busy, hot, onPatch }: Body) {
  // The analysis model is fixed once the photo is analyzed, so it is not here.
  // 張數不在這裡：對話模式一次只出一張，多張目前不支援。
  const val = (e: Event) => (e.currentTarget as HTMLSelectElement).value;
  const field = (key: string) => `chat-field${hot.has(key) ? " is-ai-hot" : ""}`;
  return (
    <div class="chat-settings-editor">
      <div class="chat-settings-fields">
        <label class={field("render_option")}>
          <span class="chat-meta">美化引擎</span>
          <select class="quality-select" value={plan.render_option} disabled={busy} onChange={(e) => onPatch({ render_option: val(e) })}>
            {caps.render.options.map((o) => (
              <option value={o.id}>{renderOptionLabel(caps, o.id)}</option>
            ))}
          </select>
        </label>
        <label class={field("aspect_ratio")}>
          <span class="chat-meta">長寬比</span>
          <select class="quality-select" value={plan.aspect_ratio} disabled={busy} onChange={(e) => onPatch({ aspect_ratio: val(e) })}>
            {caps.render.aspect_ratios.map((a) => (
              <option value={a.value}>{a.label}</option>
            ))}
          </select>
        </label>
      </div>
      <p class="chat-note">換長寬比會馬上依新的長寬比重新構圖，畫面建議裡的構圖也會跟著換。</p>
      {caps.advanced_options.length > 0 && (
        <div class="chat-settings-portrait">
          <span class="chat-meta">人像修正</span>
          <p class="chat-note">這幾項預設會偵測並修正，不想被改到的先關掉。</p>
          {caps.advanced_options.map((o) => {
            const on = plan.advanced_options?.[o.key] ?? o.default;
            return (
              <label class={`chat-switch-row${hot.has(`advanced_options.${o.key}`) ? " is-ai-hot" : ""}`}>
                <span class="ios-switch">
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={busy}
                    onChange={(e) => onPatch({ advanced_options: { [o.key]: (e.currentTarget as HTMLInputElement).checked } })}
                  />
                  <span class="ios-track">
                    <span class="ios-thumb" />
                  </span>
                </span>
                <span>{o.label}</span>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}
