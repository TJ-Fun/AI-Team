# Technical overview

Record only confirmed details. Update this when the implementation or environment changes.

## Runtime and hosting

- **Web frontend**: SvelteKit on Cloudflare Workers using the official `@sveltejs/adapter-cloudflare`.
  - `wrangler.toml` must point at the SvelteKit build output: `directory = ".svelte-kit/cloudflare"` and `main = ".svelte-kit/cloudflare/_worker.js"`.
  - Adapter and `wrangler` are dev dependencies; adapter is configured in `svelte.config.js` (SvelteKit 2) or `vite.config.js` (SvelteKit 3).
  - Deploys via Cloudflare Git integration (GitHub repo already connected; builds pass).
  - Free tier: 100k requests/day, zero egress, commercial use allowed.
- **API server**: Node.js + Fastify on Oracle Cloud ARM Always Free instance (2 OCPU, 12 GB RAM, always-on). No database installed on the VM — it is a stateless API/proxy layer only.
- **Android app**: Kotlin + Room (local SQLite). Syncs exclusively through the Fastify API — never direct database access.

## Languages and frameworks

- TypeScript + SvelteKit (Svelte) for the web app.
- Node.js + Fastify (TypeScript) for the API server.
- Kotlin + Room for Android.

## Architecture and integrations

- Three deployables: SvelteKit/Workers frontend, Fastify/Oracle API, Kotlin/Android app. Repo layout (monorepo vs separate repos) to be confirmed; if separate, map modules to repositories in the project config.
- Fastify API responsibilities: external API proxying, rate limiting, caching, Android sync endpoints.
- Frontend talks to the Fastify API; Android talks to the Fastify API; nothing else talks to the database directly.
- Bot protection: Cloudflare Turnstile (free, unlimited) on public forms/endpoints.

## Data and storage

- **Database + Auth**: Neon PostgreSQL free tier (1 GB/project, 100 CU-hours, autoscale-to-zero) with bundled Managed Better Auth (60k MAU, Google/Facebook OAuth, magic link, email/password). Do not implement custom auth.
- **Media**: Cloudflare R2 (10 GB free, zero egress) with presigned uploads. Cloudinary is an optional fallback for transformations only.
- **Caching**: global queue + TTL cache for external API responses — artist metadata 7 days, search results 24 hours.

## External APIs (rate limits — enforce strictly)

- **MusicBrainz**: 1 req/sec, User-Agent header required.
- **Setlist.fm**: 2 req/sec, 1440 req/day.
- **Discogs**: 60 req/min.
- All external calls go through the Fastify API layer with a shared global queue and TTL cache; clients never call these APIs directly.

## How to build, run, and check

- Scaffold: `npm create svelte@latest` (SvelteKit), then `npm install`, `npm run dev`, `npm run check`, `npm run build`.
- Cloudflare preview: `npx wrangler dev` / `npx wrangler deploy` (requires `CLOUDFLARE_API_TOKEN` or `wrangler login`).
- Fastify API: standard Node.js — `npm install`, `npm run dev`, `npm test` (exact scripts TBD once repo exists).
- Android: Gradle build via Android Studio / `./gradlew assembleDebug`.
- Cloudflare Git integration already builds the connected repo on push; keep `wrangler.toml` and adapter config committed.

## Known limitations

- Neon free tier: 1 GB storage, 100 CU-hours/month, autoscale-to-zero (cold starts possible).
- Better Auth managed: 60k MAU cap.
- Cloudflare Workers free: 100k req/day.
- R2 free: 10 GB storage, zero egress.
- Oracle Always Free ARM: fixed 2 OCPU / 12 GB — capacity can be hard to provision; have a fallback region/account ready.
- External API quotas are hard limits; design UI/UX around cache hits and graceful rate-limit responses.
