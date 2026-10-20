import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import preact from "@preact/preset-vite";

const OLD_INDEX = resolve(__dirname, "../frontend/index.html");

// The old page keeps a large part of its styling in an inline <style> block
// of frontend/index.html rather than in styles.css. Copy that block into the
// chat page at build time so both pages always render the shared classes the
// same way, without forking the CSS.
function oldInlineStyles(): Plugin {
  return {
    name: "old-inline-styles",
    transformIndexHtml(html) {
      const old = readFileSync(OLD_INDEX, "utf8");
      const head = old.slice(0, old.indexOf("</head>"));
      const blocks = head.match(/<style>[\s\S]*?<\/style>/g) || [];
      return html.replace("<!-- old-inline-styles -->", blocks.join("\n"));
    },
  };
}

export default defineConfig({
  base: "./",
  plugins: [preact(), oldInlineStyles()],
  build: {
    outDir: resolve(__dirname, "../frontend/chat"),
    emptyOutDir: true,
    assetsDir: "static",
  },
  server: {
    // `npm run dev` proxies API calls to a running backend.
    proxy: { "/api": process.env.PHOTOAGENT_BACKEND || "http://127.0.0.1:8997" },
  },
});
