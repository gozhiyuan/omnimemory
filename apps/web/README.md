## Run Locally

**Prerequisites:** Node.js 20+, backend services running via `make dev-up`, FastAPI API running on
http://localhost:8000 (see `services/api/README.md`). A Gemini API key is only required if you
plan to use the Chat tab. Uploads require a storage provider that supports presigned URLs (S3
compat with `STORAGE_PROVIDER=s3` + `S3_*` settings, or Supabase with `STORAGE_PROVIDER=supabase`).

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

   To enable Authentik OIDC, add the SPA client settings (matching the API `AUTH_ENABLED=true`
   config):

   ```
   VITE_OIDC_ISSUER_URL=https://authentik.example.com/application/o/omnimemory/
   VITE_OIDC_CLIENT_ID=your-client-id
   VITE_OIDC_REDIRECT_URI=http://localhost:5173
   VITE_OIDC_SCOPES=openid profile email offline_access
   ```

   If your Authentik deployment uses non-standard endpoints, you can also set:

   ```
   VITE_OIDC_AUTH_URL=...
   VITE_OIDC_TOKEN_URL=...
   VITE_OIDC_LOGOUT_URL=...
   VITE_OIDC_POST_LOGOUT_REDIRECT_URI=http://localhost:5173
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
