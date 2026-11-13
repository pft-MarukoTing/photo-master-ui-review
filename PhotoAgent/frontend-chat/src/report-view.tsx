// 管理者從舊版回報清單「查看對話」打開的頁面（/chat/#report=<report_id>）：把回報
// 的那段對話照原樣攤開，唯讀。資料走 /chat/report-session（只有管理者能讀、讀的是
// 對話主人的 session、不推進也不同步），畫面直接用 SessionView，只是 busy 一律為
// true 讓所有按鈕停用，輸入框和「對話｜面板」拿掉；面板模式改從標頭的按鈕切，打開的
// 是舊版這筆回報的畫面（#report=<id>）。
// 多張上傳的回報帶回整批：左邊照樣是「我的照片」，可以切到別張或總覽看，一開停在
// 被回報的那張；在總覽回報的（只有 batch_id）就停在總覽。
// 預設看的是回報當下存下來的那份對話，使用者回報之後再做的操作不會混進來；標頭的
// 「回報時｜現在」可以切到對話現在的樣子。這功能上線前的回報沒存，只能看現在的。
// 在首頁、上傳頁回報的還沒有對話，改看回報當下記下的頁面（report-page.ts）。
import { useLayoutEffect, useEffect, useState } from "preact/hooks";
import { api, setUser, urls, type ReportRef } from "./api";
import ycoLogoSymbol from "./assets/yco/logo-symbol.svg";
import { YcoWordmark } from "./home";
import {
  activePhoto,
  AskBubble,
  BatchPreSidebar,
  BatchSidebar,
  renderFailedIn,
  SessionView,
  sourceSrc,
  type BatchRun,
  type OverviewTurn,
  type SavedOverview,
  type Source,
} from "./app";
import { AssistantMsg, BatchUploadBubble, ChatPanel, ClickBubble, UserMsg } from "./chat";
import { resolveUser } from "./identity";
import { Icon } from "./icons";
import type { ReportPage } from "./report-page";
import { leaveReport, ReportsButton, saveStatus, StatusPill } from "./reports";
import { StepIndicator } from "./results";
import { settingsChanges } from "./settings";
import { clockLabel } from "./util";
import type { Analysis, Capabilities, Photo, Session, Version } from "./types";

const noop = async () => false;
const OVERVIEW = "overview";

function reportLabel(ts: number, live: boolean) {
  const d = new Date(ts * 1000);
  return `回報於 ${d.getMonth() + 1}/${d.getDate()} ${clockLabel(ts)}${live ? " · 目前狀態" : ""}`;
}

const photoKey = (sessionId: string, photoId: string) => `${sessionId}:${photoId}`;

type Loaded = {
  sessions: Session[];
  overview: SavedOverview | null;
  report: ReportRef;
  // live：這份是對話現在的樣子（不是回報當下存的）；kept：有沒有存回報當下那份。
  live: boolean;
  kept: boolean;
  caps: Capabilities;
  debugUi: boolean;
  analyses: Record<string, Analysis>;
  // 首頁、上傳頁的回報：沒有 session，只有當時頁面上的東西。
  page: ReportPage | null;
};

// 回報的那一刻：時間軸上標一條線，底下是回報說明。
function ReportMark({ report, live }: { report: ReportRef; live: boolean }) {
  return (
    <div class="chat-report-mark" id="chat-report-mark">
      <span class="chat-report-mark-line">{reportLabel(report.ts, live)}</span>
      {report.note && <p class="chat-report-mark-note">{report.note}</p>}
    </div>
  );
}

export function ReportView({ reportId }: { reportId: string }) {
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState("");
  // 正在看的：OVERVIEW，或 photoKey(session, photo)。
  const [view, setView] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  // 想看現在的樣子；沒存回報當下那份的，後端照樣回現在的（data.live 才是實際看到的）。
  const [wantLive, setWantLive] = useState(false);
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    let stale = false;
    setSwitching(true);
    (async () => {
      try {
        setUser(await resolveUser());
        const [{ sessions, overview, report, live, kept, page }, caps, cfg] = await Promise.all([
          api.reportSession(reportId, wantLive),
          api.capabilities(),
          api.config(),
        ]);
        // 分析一次抓齊再畫：整串對話一次到位，打開時才捲得到回報的那一則。
        const loaded = await Promise.all(
          sessions.flatMap((s) =>
            s.photos.map((p) =>
              api.reportAnalysis(reportId, s.session_id, p.photo_id, live).then(
                (r) => [photoKey(s.session_id, p.photo_id), r?.status === "success" ? (r.result?.analysis as Analysis) : null] as const,
                () => [photoKey(s.session_id, p.photo_id), null] as const,
              ),
            ),
          ),
        );
        const analyses: Record<string, Analysis> = {};
        for (const [k, a] of loaded) if (a) analyses[k] = a;
        const reported = sessions.find((s) => s.session_id === report.session_id);
        const photo = reported && (reported.photos.find((p) => p.photo_id === report.photo_id) || activePhoto(reported));
        const first = sessions[0] && activePhoto(sessions[0]);
        if (stale) return;
        // 切換「回報時｜現在」時，左邊停在哪張就留在哪張（那張還在的話）。
        setView((prev) =>
          prev && (prev === OVERVIEW ? !!report.batch_id : sessions.some((s) => s.photos.some((p) => prev === photoKey(s.session_id, p.photo_id))))
            ? prev
            : reported
            ? photo ? photoKey(reported.session_id, photo.photo_id) : null
            : report.batch_id
              ? OVERVIEW
              : first ? photoKey(sessions[0].session_id, first.photo_id) : null,
        );
        // 打開一筆未讀的就算讀過了，跟從清單點進來一樣（舊版「查看對話」的連結不會改狀態）。
        if (!wantLive && (report.status || "unread") === "unread") saveStatus(report, "read", () => {});
        setData({ sessions, overview: (overview as SavedOverview | null) || null, report, live, kept, caps, debugUi: !!cfg.debugUi, analyses, page: page || null });
        setSelected(null);
        setSwitching(false);
      } catch (e) {
        if (stale) return;
        setSwitching(false);
        const status = (e as { status?: number }).status;
        setError(
          status === 403 ? "只有管理者能查看回報的對話。" : status === 404 ? "這筆回報沒有對應的對話，或對話已經刪除。" : "對話讀取失敗，請重新整理再試一次。",
        );
      }
    })();
    return () => {
      stale = true;
    };
  }, [reportId, wantLive]);

  const report = data?.report;
  // 面板模式跟著左邊正在看的那張走。回報的那張用回報自己的 job（帶著當時的設定）；
  // 批次裡的其他張、或在總覽回報的（沒有 job），改開那張目前的版本，沒美化過就開它的分析。
  const [viewSession, viewPhoto] = view && view !== OVERVIEW ? view.split(":") : [];
  const shown = data?.sessions.find((s) => s.session_id === viewSession)?.photos.find((p) => p.photo_id === viewPhoto);
  const panelHref = !report
    ? null
    : view === OVERVIEW || !shown
      ? report.job_id
        ? urls.oldUiReport(report.report_id)
        : null
      : report.job_id && viewSession === report.session_id && viewPhoto === report.photo_id
        ? urls.oldUiReport(report.report_id)
        : shown.active_version?.job_id
          ? urls.oldUiReport(report.report_id, { job: shown.active_version.job_id })
          : shown.analyze_job_id
            ? urls.oldUiReport(report.report_id, { analyze: shown.analyze_job_id })
            : null;
  const pick = (key: string) => {
    setView(key);
    setSelected(null);
  };

  return (
    <>
      {/* 跟其他頁同一款 YCO header（home.tsx 的 Header）：左邊 YouCam Online Editor logo，右邊是回報檢視自己的工具。 */}
      <header class="site-header yco-header">
        <div class="header-inner">
          <button type="button" class="logo yco-brand" aria-label="YouCam Online Editor" onClick={leaveReport}>
            <img class="yco-logo-symbol" src={ycoLogoSymbol} alt="" aria-hidden="true" />
            <YcoWordmark />
          </button>
          {data && report && (
            <div class="report-view-meta">
              <span class="report-view-badge">唯讀</span>
              <span class="report-view-owner">{report.owner} 的對話</span>
              <StatusPill report={report} />
              {!data.page && <nav class="mode-switch report-view-when" aria-label="看哪個時間點的對話">
                <button
                  type="button"
                  class={`mode-switch-opt${!data.live ? " is-active" : ""}`}
                  aria-pressed={!data.live}
                  disabled={!data.kept || switching}
                  title={data.kept ? "看使用者按下回報那一刻的對話" : "這筆回報送出時還沒有保存當下的對話，只能看現在的樣子"}
                  onClick={() => setWantLive(false)}
                >
                  回報時
                </button>
                <button
                  type="button"
                  class={`mode-switch-opt${data.live ? " is-active" : ""}`}
                  aria-pressed={data.live}
                  disabled={switching}
                  title="看這段對話現在的樣子，包含回報之後的操作"
                  onClick={() => setWantLive(true)}
                >
                  現在
                </button>
              </nav>}
            </div>
          )}
          <div class="report-view-actions">
            <ReportsButton currentId={reportId} />
            {panelHref ? (
              <a class="ycoButton variant_secondary tone_neutral size_tiny" href={panelHref} title="用面板模式看左邊這張">
                <span class="buttonIcon"><Icon name="layout" size={14} /></span>
                <span class="label">面板模式</span>
              </a>
            ) : (
              <a class="ycoButton variant_secondary tone_neutral size_tiny is-disabled" aria-disabled="true" title={report ? (data?.page ? "在首頁或上傳頁回報的，還沒有面板可看" : view === OVERVIEW ? "先在左邊選一張，才有面板可看" : "這張還沒有分析結果，沒有面板可看") : undefined}>
                <span class="buttonIcon"><Icon name="layout" size={14} /></span>
                <span class="label">面板模式</span>
              </a>
            )}
            <button class="ycoButton variant_secondary tone_neutral size_tiny" type="button" title="回到聊天" onClick={leaveReport}>
              <span class="buttonIcon"><Icon name="chat" size={14} /></span>
              <span class="label">回到聊天</span>
            </button>
          </div>
        </div>
      </header>
      <main>
        {error && <p class="report-view-status">{error}</p>}
        {!error && !data && <p class="report-view-status">正在讀取對話…</p>}
        {data?.page && <PageReport page={data.page} report={data.report} />}
        {data && !data.page && !view && <p class="report-view-status">這段對話裡沒有照片。</p>}
        {data && view && <ReportBody data={data} view={view} selected={selected} onPick={pick} onSelect={setSelected} />}
      </main>
    </>
  );
}

function ReportBody(props: {
  data: Loaded;
  view: string;
  selected: string | null;
  onPick: (key: string) => void;
  onSelect: (versionId: string) => void;
}) {
  const { data, view } = props;
  const { sessions, report } = data;
  // 多張上傳：每張一個 session，左邊列成「我的照片」，最上面是總覽。
  const batch = !!report.batch_id && (sessions.length > 1 || !!data.overview || view === OVERVIEW);
  const members = sessions.flatMap((s) => {
    const p = activePhoto(s);
    if (!p) return [];
    const source: Source = { kind: "remote", job: p.analyze_job_id };
    return [{ session: s, photo: p, source, key: sourceSrc(source) }];
  });
  const runs: Record<string, BatchRun> = {};
  const live: Record<string, Photo> = {};
  const failed: Record<string, boolean> = {};
  for (const m of members) {
    runs[m.key] = { mode: m.photo.active_version ? "render" : "analyze", since: 0, lines: [], sessionId: m.session.session_id, photoId: m.photo.photo_id, done: true };
    live[m.key] = m.photo;
    failed[m.key] = renderFailedIn(m.session, m.photo.photo_id);
  }
  const here = members.find((m) => photoKey(m.session.session_id, m.photo.photo_id) === view);
  const batchSidebar = batch && (
    <BatchPreSidebar
      sources={members.map((m) => m.source)}
      runs={runs}
      live={live}
      failed={failed}
      wished={() => false}
      current={view === OVERVIEW ? null : here?.key || null}
      pending={0}
      renderable={0}
      disabled={false}
      overview={view === OVERVIEW}
      onOverview={() => props.onPick(OVERVIEW)}
      onPick={(s) => {
        const m = members.find((x) => x.key === sourceSrc(s));
        if (m) props.onPick(photoKey(m.session.session_id, m.photo.photo_id));
      }}
      onStartAll={() => {}}
    />
  );

  if (view === OVERVIEW) {
    return (
      <>
        <StepIndicator step={1} state="done" pre />
        <section id="upload-section" class="section visible">
          <div class="container upload-container has-flow">
            <div class="upload-grid">
              <div class="upload-content-row is-chat">
                {batchSidebar}
                <OverviewLog data={data} thumbs={new Map(members.map((m) => [m.session.session_id, m.key]))} />
              </div>
            </div>
          </div>
        </section>
      </>
    );
  }

  const [sessionId, photoId] = view.split(":");
  const session = sessions.find((s) => s.session_id === sessionId);
  const photo = session?.photos.find((p) => p.photo_id === photoId);
  if (!session || !photo) return <p class="report-view-status">這段對話裡沒有照片。</p>;
  // 回報的線只畫在被回報的那段對話裡。
  const marked = session.session_id === report.session_id;
  return (
    <SessionView
      key={view}
      sidebar={
        batchSidebar ||
        (session.photos.length > 1 && (
          <BatchSidebar
            photos={session.photos}
            messages={session.messages || []}
            current={photo.photo_id}
            onPick={(id) => props.onPick(photoKey(session.session_id, id))}
            busy
            onRenderAll={() => {}}
          />
        ))
      }
      caps={data.caps}
      debugUi={data.debugUi}
      session={session}
      photo={photo}
      analysis={data.analyses[view] || null}
      analysisPending={false}
      selected={props.selected}
      pendingAsk={null}
      busy
      error=""
      readOnly={marked ? { reportAt: report.ts, label: reportLabel(report.ts, data.live), note: report.note } : {}}
      onSelectVersion={(v: Version) => props.onSelect(v.version_id)}
      onRender={() => {}}
      onFile={() => {}}
      onAsk={noop}
      onAnswerWaiting={() => {}}
      onPatch={noop}
      onQuietPrompt={noop}
      onPost={noop}
      onAnalyze={noop}
      onOpenStyle={noop}
      onResolve={noop}
    />
  );
}

// 總覽說過的話，唯讀：照存下來的那幾輪重講一次，不放任何按鈕。講法跟聊天頁的總覽
// 同一套，只是按鈕那幾句改成直接寫使用者選了什麼。
function OverviewLog({ data, thumbs }: { data: Loaded; thumbs: Map<string, string> }) {
  const { overview, report, caps } = data;
  const turns = overview?.turns || [];
  const shots = (ids: string[]) => ids.map((id) => thumbs.get(id)).filter((x): x is string => !!x);
  const first = turns[0]?.kind === "upload" ? turns[0] : null;
  const firstCount = first ? first.srcs.length : thumbs.size;
  // 總覽每一輪的 id 是當下的毫秒數：回報落在第一輪比它晚的前面，都比它早就在最後。
  const at = report.session_id ? -1 : ((i) => (i < 0 ? turns.length : i))(turns.findIndex((t) => t.id > report.ts * 1000));
  useLayoutEffect(() => {
    const mark = document.getElementById("chat-report-mark");
    const timeline = mark?.closest(".chat-timeline");
    if (!mark || !timeline) return;
    timeline.scrollTop += mark.getBoundingClientRect().top - timeline.getBoundingClientRect().top - timeline.clientHeight / 3;
  }, []);
  const said = (t: OverviewTurn, i: number) => {
    if (t === first) return null;
    switch (t.kind) {
      case "upload":
        return (
          <div key={t.id}>
            <UserMsg><BatchUploadBubble srcs={shots(t.srcs)} text={`新增了 ${t.srcs.length} 張照片`} /></UserMsg>
            <AssistantMsg><p class="chat-text">好，加進來了。</p></AssistantMsg>
          </div>
        );
      case "remove":
        return (
          <AssistantMsg key={t.id}>
            <p class="chat-text chat-muted">{`移除了 ${t.count} 張，現在一共 ${t.left} 張。`}</p>
          </AssistantMsg>
        );
      case "running":
        return (
          <AssistantMsg key={t.id}>
            <p class="chat-text">{`有 ${t.keys.length} 張開始${t.verb}了。`}</p>
          </AssistantMsg>
        );
      case "done": {
        const parts = [
          t.rendered && `${t.rendered} 張美化好了`,
          t.analyzed && `${t.analyzed} 張分析好了`,
          t.failed && `${t.failed} 張沒有成功`,
        ].filter(Boolean);
        return (
          <AssistantMsg key={t.id}>
            <p class="chat-text">{`剛剛送出的都處理完成了${parts.length ? `，目前 ${parts.join("、")}` : ""}。`}</p>
          </AssistantMsg>
        );
      }
      case "wish":
        return (
          <div key={t.id} class="note-turn">
            <AskBubble text={t.text} />
            {t.pending ? null : t.kept === false ? (
              t.reply ? (
                <AssistantMsg>
                  <p class="chat-text">{t.reply}</p>
                  {!!t.options?.length && <p class="chat-text chat-muted">{`給的選項：${t.options.join("、")}`}</p>}
                </AssistantMsg>
              ) : null
            ) : (
              <AssistantMsg>
                <p class="chat-text">
                  {t.count && t.reply
                    ? t.reply
                    : `${t.line ? `好，記下了：${t.line}。` : ""}${t.count
                      ? `${t.line ? "" : "好，"}這句會套用在 ${t.count} 張還沒美化的照片上。${t.skipped ? `已經在美化或美化好的 ${t.skipped} 張不會套用。` : ""}`
                      : "每張都已經在美化或美化好了，這句不會套用。"}`}
                </p>
                {t.gone && <p class="chat-text chat-muted">這句後來撤回或被新的需求取代了，已經從每張拿掉。</p>}
              </AssistantMsg>
            )}
          </div>
        );
      case "settings": {
        const changed = caps && t.from && t.settings ? settingsChanges(caps, t.from, t.settings, t.model) : [];
        return (
          <div key={t.id} class="settings-turn">
            <UserMsg><ClickBubble icon="tune" label="設定" /></UserMsg>
            <AssistantMsg>
              <p class="chat-text">{changed.length ? `好，${changed.join("、")}，會套用到每張還沒美化的照片。` : "設定沒有改動。"}</p>
            </AssistantMsg>
          </div>
        );
      }
      case "ask":
        return (
          <div key={t.id}>
            <UserMsg><ClickBubble icon="spark" label={t.label || "全部一鍵美化"} /></UserMsg>
            <AssistantMsg>
              <p class="chat-text">有幾張講了需求，要整體美化，還是僅依需求修改？</p>
            </AssistantMsg>
            {t.overwrite !== undefined && <UserMsg><ClickBubble icon="spark" label={t.overwrite ? "僅依需求修改" : "整體美化"} /></UserMsg>}
          </div>
        );
      case "start":
        return (
          <div key={t.id}>
            {turns[i - 1]?.kind !== "ask" && (
              <UserMsg><ClickBubble icon={t.mode === "analyze" ? "search" : "spark"} label={t.label || (t.mode === "analyze" ? "全部分析" : "全部一鍵美化")} /></UserMsg>
            )}
            <AssistantMsg>
              <p class="chat-text">{`好，${t.count} 張開始${t.mode === "analyze" ? "分析" : t.overwrite ? "調整" : "美化"}了。`}</p>
            </AssistantMsg>
          </div>
        );
    }
  };
  const items = turns.map(said);
  return (
    <ChatPanel class="home-chat is-overview" sub="總覽" footer={null} live={false}>
      <UserMsg>
        <BatchUploadBubble srcs={first ? shots(first.srcs) : [...thumbs.values()]} text={`上傳了 ${firstCount} 張照片`} />
      </UserMsg>
      <AssistantMsg>
        <p class="chat-text">{`收到你上傳的 ${firstCount} 張照片了。`}</p>
        {!overview && <p class="chat-text chat-muted">總覽的對話沒有留下紀錄（可能已經過期），只看得到各張自己的對話。</p>}
      </AssistantMsg>
      {at < 0 ? items : <>{items.slice(0, at)}<ReportMark report={report} live={data.live} />{items.slice(at)}</>}
    </ChatPanel>
  );
}

// 首頁、上傳頁的回報：照記下來的頁面排回去。首頁只有一句話；上傳頁左邊是當時的照片，
// 右邊是聊天區每一則的文字和圖，按鈕改寫成「給的選項」，最後是回報的線。
function PageReport({ page, report }: { page: ReportPage; report: ReportRef }) {
  if (page.page === "home") {
    return (
      <div class="report-view-page-home">
        <p class="report-view-status">在首頁回報的，當時還沒開始對話。</p>
        <ReportMark report={report} live={false} />
      </div>
    );
  }
  const photos = page.photos || [];
  const total = page.total || photos.length;
  const more = total - photos.length;
  return (
    <>
      {total > 0 && <StepIndicator step={1} state="done" pre />}
      <section id="upload-section" class="section visible">
        <div class={`container upload-container${total > 0 ? " has-flow" : ""}`}>
          <div class="upload-grid">
            <div class="upload-content-row is-chat">
              {total > 1 ? (
                <aside class="batch-sidebar" id="batch-sidebar">
                  <div class="batch-sidebar-header">
                    <h2 class="batch-sidebar-title">我的照片</h2>
                  </div>
                  <div class="batch-sidebar-list" aria-label="當時的照片">
                    {photos.map((src, i) => (
                      <span key={i} class="history-cube batch-sidebar-item">
                        <span class="rc-media"><img src={src} alt="" /></span>
                      </span>
                    ))}
                    {more > 0 && <p class="chat-text chat-muted">{`還有 ${more} 張沒有記下來`}</p>}
                  </div>
                </aside>
              ) : photos[0] ? (
                <div class="prechat-stage">
                  <div class="prechat-shot"><img src={photos[0]} alt="當時的照片" /></div>
                </div>
              ) : null}
              <ChatPanel class="home-chat" sub="上傳頁" footer={null} live={false}>
                {(page.messages || []).map((m, i) =>
                  m.user ? (
                    <UserMsg key={i}>
                      {m.images.length ? (
                        <BatchUploadBubble srcs={m.images} text={m.lines.join(" ")} />
                      ) : (
                        <div class="chat-bubble">{m.lines.join(" ") || m.chips.join("、")}</div>
                      )}
                    </UserMsg>
                  ) : (
                    <AssistantMsg key={i}>
                      {m.images.length > 0 && (
                        <div class="chat-upload-stack" aria-hidden="true">
                          {m.images.map((src, j) => <img key={j} src={src} alt="" />)}
                        </div>
                      )}
                      {m.lines.map((l, j) => <p key={j} class="chat-text">{l}</p>)}
                      {m.chips.length > 0 && <p class="chat-text chat-muted">{`給的選項：${m.chips.join("、")}`}</p>}
                    </AssistantMsg>
                  ),
                )}
                {!!page.settings?.length && (
                  <AssistantMsg>
                    <p class="chat-text chat-muted">{`當時的設定：${page.settings.join("、")}`}</p>
                  </AssistantMsg>
                )}
                <ReportMark report={report} live={false} />
              </ChatPanel>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
