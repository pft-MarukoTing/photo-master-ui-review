// Made by the l10n-by-language plugin in vite.config.ts from src/generated/l10n.json.
declare module "virtual:l10n" {
  export const languages: { code: string; name: string; fallback?: string }[];
  export const catalogs: Record<string, () => Promise<{ default: Record<string, string> }>>;
}
