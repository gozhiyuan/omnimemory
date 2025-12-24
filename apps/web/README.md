## Run Locally

**Prerequisites:** Node.js 20+, backend services running via `make dev-up`, FastAPI API running on
http://localhost:8000 (see `services/api/README.md`). A Gemini API key is only required if you
plan to use the Chat tab. Uploads require Supabase storage credentials (`STORAGE_PROVIDER=supabase`
with `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`).

1. Install dependencies (inside `apps/web/`):

   ```bash
   npm install
   ```

   If `npm` reports cache permission errors, ensure `~/.npm` is owned by your user or pass a
   project-local cache path: `npm install --cache .npm-cache`.

2. Create `.env.local` based on `.env.example` (or the snippet below) and set the API base URL. Add
   `GEMINI_API_KEY` if you want chat responses. Example:

   ```
   VITE_API_URL=http://localhost:8000
   GEMINI_API_KEY=your-key-here
   ```

3. Start the Vite dev server:

   ```bash
   npm run dev
   ```

  The app opens at http://localhost:5173 and expects the FastAPI backend + Celery worker to be
  running so uploads, timeline, and dashboard data can fetch live data. The Chat tab calls Gemini
  directly from the browser using mock memory context (backend chat endpoints are not wired yet).

## Tests

Run the Playwright smoke test (requires the API, Celery worker, and Supabase storage enabled):

```bash
npm run test:e2e
```
