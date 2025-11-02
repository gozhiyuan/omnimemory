## OmniMemory Monorepo

This repository contains the source code for the Lifelog AI platform, OmniMemory.

### Project Structure

- `services/`: Backend microservices.
  - `api/`: The main FastAPI application for handling user requests, authentication, and task queuing.
  - `workers/`: The Celery workers responsible for all asynchronous background processing (e.g., OCR, embedding, summarization).
- `apps/`: User-facing client applications.
  - `web/`: The Next.js web application.
  - `mobile/`: The React Native (Expo) mobile application.
  - `desktop/`: The Electron desktop agent for automatic data capture.
- `docker-compose.yml`: Defines the local development environment.
- `DEVELOPMENT_PLAN.md`: Outlines the project roadmap and phases.

### Getting Started

1.  Ensure Docker is running.
2.  Copy `.env.example` to `.env` and fill in the required values.
3.  Run `docker-compose up --build` to start the backend services.
4.  Navigate to `apps/web` and run `pnpm install && pnpm dev` to start the web frontend.
