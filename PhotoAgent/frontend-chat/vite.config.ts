import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import preact from "@preact/preset-vite";

const OLD_INDEX = resolve(__dirname, "../frontend/index.html");
const L10N = resolve(__dirname, "src/generated/l10n.json");
const L10N_ID = "virtual:l10n";

// Serves src/generated/l10n.json as "virtual:l10n" (the language list, and a loader per language)
// and one "virtual:l10n/<code>" module per catalog, so each language is its own chunk.
function l10nByLanguage(): Plugin {
  return {
    name: "l10n-by-language",
    resolveId(id) {
      if (id === L10N_ID || id.startsWith(`${L10N_ID}/`)) return `\0${id}`;
    },
    load(id) {
      if (!id.startsWith(`\0${L10N_ID}`)) return;
      this.addWatchFile(L10N);
      const made = existsSync(L10N) ? JSON.parse(readFileSync(L10N, "utf8")) : { languages: [], catalogs: {} };
      const code = id.slice(L10N_ID.length + 2);
      if (code) return `export default ${JSON.stringify(made.catalogs[code] || {})};`;
      const loaders = made.languages.map((l: { code: string }) =>
        `${JSON.stringify(l.code)}: () => import(${JSON.stringify(`${L10N_ID}/${l.code}`)})`);
      return `export const languages = ${JSON.stringify(made.languages)};\nexport const catalogs = {${loaders.join(",")}};`;
    },
    handleHotUpdate({ file, server }) {
      if (file !== L10N) return;
      server.moduleGraph.invalidateAll();
      server.ws.send({ type: "full-reload" });
      return [];
    },
  };
}

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
  plugins: [preact(), oldInlineStyles(), l10nByLanguage()],
  build: {
    // src/i18n.ts waits for its catalog with a top-level await.
    target: ["chrome89", "edge89", "firefox89", "safari15"],
    outDir: resolve(__dirname, "../frontend/chat"),
    emptyOutDir: true,
    assetsDir: "static",
  },
  server: {
    // `npm run dev` proxies API calls to a running backend.
    proxy: { "/api": process.env.PHOTOAGENT_BACKEND || "http://127.0.0.1:8997" },
  },
});
