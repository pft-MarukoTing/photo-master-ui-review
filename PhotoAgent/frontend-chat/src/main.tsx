import { render } from "preact";
import { App } from "./app";
import { UpdateBanner } from "./update";
import "./chat.css";

render(
  <>
    <App />
    <UpdateBanner />
  </>,
  document.getElementById("app")!,
);
