# Steward console

React, TypeScript, Vite and Tailwind v4. Console v0 (Phase 1): the agents with their pass, model, tools and pin state, spend today, the live flight log, and the chain status with export.

Steward serves the built files at `/console` from `apps/web/dist`, so build before starting Steward. The console calls the admin API with the owner token (`STEWARD_OWNER_TOKEN`), which you paste once; it stays in the browser tab's `sessionStorage`.

```bash
pnpm install
pnpm lint
pnpm build
pnpm dev     # http://localhost:5173/console/, proxies /admin to a Steward on localhost:8000
```

pnpm is pinned in `package.json` (`packageManager`). Without a global pnpm, prefix the commands with `corepack`, for example `corepack pnpm build`.

| File | What it holds |
| --- | --- |
| `src/api.ts` | Typed admin API client, and the live log reader (server-sent events read with fetch, because EventSource cannot send the owner token) |
| `src/pages.tsx` | Agents, Agent detail, Live log and Chain pages |
| `src/format.ts` | One-line summaries of log events |
| `src/App.tsx` | Sign-in, header and hash routes (`#/agents`, `#/agents/<id>`, `#/log`, `#/chain`) |
