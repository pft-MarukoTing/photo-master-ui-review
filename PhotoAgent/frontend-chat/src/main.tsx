import { render } from "preact";
import { App } from "./app";
import { ReportView } from "./report-view";
import { useReportRoute } from "./reports";
import { UpdateBanner } from "./update";
import "./chat.css";

// #report=<id>: 管理者從回報清單打開的唯讀對話，不進一般的聊天流程。清單在同一頁換一筆、
// 或「回到聊天」都只換 hash，這裡跟著換畫面；換一筆就整個重讀（key）。
function Root() {
  const reportId = useReportRoute();
  return reportId ? <ReportView key={reportId} reportId={reportId} /> : <App />;
}

render(
  <>
    <Root />
    <UpdateBanner />
  </>,
  document.getElementById("app")!,
);
