# Lifelog AI Development Plan

This document outlines the development phases, milestones, and tasks for building the Lifelog AI platform.

---

## Phase 0: Project Setup & Foundation (1 Week)

**Goal:** Prepare the development environment, repositories, and core infrastructure.

- **Tasks:**
  - [ ] Initialize monorepo with `pnpm` workspaces or similar.
  - [ ] Create repository structure:
    - `services/api` (FastAPI)
    - `services/workers` (Celery)
    - `apps/web` (Next.js)
    - `apps/mobile` (React Native/Expo)
    - `apps/desktop` (Electron)
  - [ ] Implement the `docker-compose.yml` for a local development stack (Postgres, Redis, Qdrant, API, Workers).
  - [ ] Provision initial cloud services (Supabase project, Qdrant Cloud dev instance).
  - [ ] Set up basic CI/CD pipeline in GitHub Actions to build and test on push.

---

## Phase 1: Core MVP - Ingestion & Chat (4 Weeks)

**Goal:** Achieve a functional end-to-end flow: upload a file, process it, and ask a question about it.

- **Tasks:**
  - **Week 1: API & Storage**
    - [ ] Build the FastAPI upload endpoint (`/api/v1/upload`).
    - [ ] Integrate with Supabase for user authentication (JWT) and storage (presigned URLs).
    - [ ] Define and create the initial PostgreSQL schema (`users`, `files`, `chunks`).
  - **Week 2: Processing Pipeline**
    - [ ] Set up Celery worker and task queue with Redis.
    - [ ] Implement a basic preprocessing task: image captioning (e.g., using a Hugging Face model or API).
    - [ ] Generate embeddings for the caption and upsert them into Qdrant.
  - **Week 3: Web UI & RAG**
    - [ ] Develop the Next.js web app with a basic chat interface.
    - [ ] Implement user login/logout using the Supabase client.
    - [ ] Build the backend RAG logic: receive a query, retrieve relevant chunks from Qdrant, and pass them to an LLM API (e.g., Gemini/GPT).
  - **Week 4: Integration & Testing**
    - [ ] Ensure the full flow is working: upload an image, see it processed, and get a relevant answer in the chat.
    - [ ] Write initial integration tests for the pipeline.
    - [ ] Deploy the MVP to a staging environment (Vercel, Railway/Render).

---

## Phase 2: Automated Capture & Connectors (6 Weeks)

**Goal:** Expand data sources beyond manual uploads with automated agents and API connectors.

- **Tasks:**
  - [ ] **Desktop Agent:**
    - [ ] Develop a prototype Electron app for periodic screenshot capture.
    - [ ] Implement a local queue and resumable uploads to the backend.
  - [ ] **Mobile App:**
    - [ ] Create an Expo-based app for manual photo/video capture and upload.
    - [ ] (Optional) Explore background screenshot/clip capture.
  - [ ] **Connectors:**
    - [ ] Implement OAuth2 flow for Google Photos.
    - [ ] Build a sync mechanism to pull data from the Google Photos API.
  - [ ] **Scheduler:**
    - [ ] Implement a daily job scheduler (e.g., using Celery Beat or Prefect) to trigger daily summaries.

---

## Phase 3: Context Engine & Memory Graph (6 Weeks)

**Goal:** Enhance retrieval quality with a deeper understanding of context, entities, and events.

- **Tasks:**
  - [ ] **Entity Extraction:**
    - [ ] Add an LLM-based step in the processing pipeline to extract entities (people, places, topics) from text chunks.
  - [ ] **Memory Graph:**
    - [ ] Implement the `graph_nodes` and `graph_edges` tables in Postgres.
    - [ ] Create a service to populate the graph based on extracted entities and event co-occurrence.
  - [ ] **Hybrid Retrieval:**
    - [ ] Augment the RAG process to query the graph for related entities when a user asks a question.
    - [ ] Implement a scoring function that combines semantic similarity with time, location, and entity overlap.
  - [ ] **Integrate Graphiti/Zep:**
    - [ ] Use Zep to store and retrieve conversation history and entity-aware memories.

---

## Phase 4: Multimodal & Advanced Outputs (6 Weeks)

**Goal:** Leverage multimodal models and generate rich, user-facing content.

- **Tasks:**
  - [ ] **Multimodal LLMs:**
    - [ ] Upgrade the captioning/analysis pipeline to use a multimodal model like Gemini or GPT-4o for richer descriptions.
  - [ ] **Vlog Generation:**
    - [ ] Create a service that takes a daily summary and generates a video montage using `ffmpeg`.
    - [ ] Add text-to-speech narration for the vlog.
  - [ ] **Sharing & Access Control:**
    - [ ] Build the UI and backend logic for creating shareable, scoped links to memories or chatbots.
    - [ ] Implement and enforce Row-Level Security (RLS) in Supabase.

---

## Phase 5: Hardening & Scaling (4 Weeks)

**Goal:** Prepare the platform for production launch with a focus on reliability, security, and performance.

- **Tasks:**
  - [ ] **Monitoring & Alerting:**
    - [ ] Set up Prometheus/Grafana for system metrics and Sentry for error tracking.
    - [ ] Create alerts for critical issues (e.g., high queue latency, high error rates).
  - [ ] **Security Audit:**
    - [ ] Conduct a thorough security review of all services.
    - [ ] Implement rate limiting, input validation, and other hardening measures.
  - [ ] **Performance Tuning:**
    - [ ] Load test the API and workers.
    - [ ] Optimize database queries and embedding retrieval.
  - [ ] **Cost Management:**
    - [ ] Implement user quotas and storage lifecycle policies.
    - [ ] Optimize batching for AI model calls.
