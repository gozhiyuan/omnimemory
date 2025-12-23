## Run Locally

**Prerequisites:** Node.js 20+, backend services running via `make dev-up`, FastAPI API running on
http://localhost:8000 (see `services/api/README.md`), and a Gemini API key.

1. Install dependencies (inside `apps/web/`):

   ```bash
   npm install
   ```

   If `npm` reports cache permission errors, ensure `~/.npm` is owned by your user or pass a
   project-local cache path: `npm install --cache .npm-cache`.

2. Create `.env.local` based on `.env.example` (or the snippet below) and set your Gemini key and API
   base URL. Example:

   ```
   VITE_API_URL=http://localhost:8000
   GEMINI_API_KEY=your-key-here
   ```

3. Start the Vite dev server:

   ```bash
   npm run dev
   ```

   The app opens at http://localhost:5173 and expects the FastAPI backend + Celery worker to be
   running so uploads, Google Photos sync, chat, and timeline data can fetch live data.
