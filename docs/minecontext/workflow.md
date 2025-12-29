# MineContext (OpenContext) Backend: Ingestion + RAG Workflow

This document explains how the MineContext backend is implemented (capture → processing → storage → retrieval → chat), with a focus on **screenshots**, **documents**, and the **RAG workflow** used to answer user questions.

> Repo naming note: the Python backend package is `opencontext/` and is exposed via a FastAPI server.

---

## 1) High-level architecture

- **Server / API layer**: FastAPI routes in `opencontext/server/` (router wiring in `opencontext/server/api.py`).
- **Orchestrator**: `OpenContext` initializes global singletons and managers (`opencontext/server/opencontext.py`).
- **Capture layer**: optional capture components (screenshots, folder monitor, vault monitor, web link capture) emit `RawContextProperties`.
- **Processing layer**:
  - `ScreenshotProcessor` turns screenshots into multiple `ProcessedContext` items (activity/semantic/state/procedural/intent/entity).
  - `DocumentProcessor` turns documents into chunked `knowledge_context` items.
- **Storage layer**:
  - **Vector DB**: ChromaDB by default (or Qdrant) stores `ProcessedContext` with embeddings.
  - **SQLite**: stores “app data” like conversations, messages, tips, todos, activities, vault documents, etc.
- **LLM layer**:
  - A “VLM/chat” config (`vlm_model`) used for screenshot/document visual understanding and agent prompting.
  - An “embedding” config (`embedding_model`) used to vectorize stored text and query text.

Configuration is primarily `config/config.yaml` (and prompts in `config/prompts_zh.yaml`).

---

## 2) LLM integration (Do they use vLLM / omni?)

The backend does **not** hardcode vLLM or “omni” as a framework. Instead it uses the official `openai` Python SDK with a configurable `base_url` and `model`, i.e. **any OpenAI-compatible API** can be used:

- Chat / vision calls use `vlm_model` config: `base_url`, `api_key`, `model`, `provider` (`config/config.yaml`).
- Embeddings use `embedding_model` config (`config/config.yaml`).

This means:
- You can point `vlm_model.base_url` to OpenAI, Doubao, LM Studio, or any OpenAI-compatible server.
- You can point `embedding_model.base_url` to OpenAI, Doubao, or an OpenAI-compatible embedding server.
- If your local vLLM deployment exposes an OpenAI-compatible API, it can be used **via config**, but there is no vLLM-specific code path.

---

## 3) Core data model (“what is stored”)

### 3.1 Raw inputs: `RawContextProperties`

Raw events entering the processing pipeline are represented as `RawContextProperties` (`opencontext/models/context.py`), including:

- `source`: e.g. `screenshot`, `local_file`, `web_link`, `vault` (`opencontext/models/enums.py`)
- `content_format`: `image`, `file`, `text`
- `create_time`
- `content_path` (for image/file inputs)
- `content_text` (for text inputs)
- `additional_info` (source-specific metadata)

### 3.2 Processed memory: `ProcessedContext`

Processed “memory” is stored as `ProcessedContext` (`opencontext/models/context.py`):

- **`extracted_data`**:
  - `context_type`: one of:
    - `activity_context`, `semantic_context`, `procedural_context`, `state_context`, `intent_context`, `entity_context`, `knowledge_context` (`opencontext/models/enums.py`)
  - `title`, `summary`, `keywords`, `entities`
  - `importance`, `confidence`
- **`properties`**:
  - timestamps: `create_time`, `update_time`, `event_time`
  - counters: `duration_count`, `merge_count`
  - `raw_properties` list (links back to original screenshot/file events)
- **`vectorize`**:
  - `text` (the string that gets embedded and stored as the “document” in the vector DB)
  - `vector` (embedding array)

---

## 4) Screenshot ingestion pipeline (no chunking)

Screenshots are treated as an **image stream**, not a “video file”. The pipeline:

### 4.1 How a screenshot is ingested

1. Client saves a screenshot file locally (default path configured under `capture.screenshot.storage_path` in `config/config.yaml`).
2. Client calls `POST /api/add_screenshot` with:
   - `path` (local file path)
   - `window`, `create_time`, `source/app` (`opencontext/server/routes/screenshots.py`)
3. Backend validates the file exists, then creates a `RawContextProperties`:
   - `source = screenshot`
   - `content_format = image`
   - `content_path = path`
   - `additional_info` includes: `window`, `app`, `duration_count`, `screenshot_format` (`opencontext/server/context_operations.py`)
4. The `ContextProcessorManager` routes it to `screenshot_processor` (`opencontext/managers/processor_manager.py`).

### 4.2 How screenshots are processed

Inside `ScreenshotProcessor` (`opencontext/context_processing/processor/screenshot_processor.py`):

1. **Resize (optional)**: downscale to reduce costs/latency.
2. **Near-duplicate detection**:
   - computes perceptual hash (dHash) and drops near duplicates (`opencontext/utils/image.py`).
3. **Batching**:
   - a background thread groups screenshots into batches by size/timeout.
4. **VLM extraction**:
   - each screenshot is base64-encoded and sent to the VLM as an OpenAI-style multimodal chat message (`image_url` with `data:image/...;base64,...`).
   - prompt used: `processing.extraction.screenshot_analyze` in `config/prompts_zh.yaml`.
   - expected output: JSON with `items[]` where each item includes:
     - `context_type`, `title`, `summary`, `keywords`, `importance`, `confidence`
5. **How many summaries per screenshot?**
   - The prompt explicitly allows multiple `items` per screenshot and instructs:
     - always produce at least one `activity_context` per distinct activity
     - also extract semantic/state/procedural/intent items when present
   - Therefore: **1..N items per screenshot**, depending on how many topics/activities are present.
6. **Semantic merge across time**
   - Items are merged (per `context_type`) using another LLM call:
     - prompt: `merging.screenshot_batch_merging` in `config/prompts_zh.yaml`
     - output: items with `merge_type: merged|new`, `merged_ids`, and `data` containing richer fields like `entities`, `tags`, `event_time`.
   - Merge output drives:
     - deleting merged-away IDs from the vector DB
     - updating counters: `duration_count`, `merge_count`
7. **Entities**
   - Entities from the merge output are normalized and used to maintain `entity_context` entries (profiles + relationships) (`opencontext/context_processing/processor/entity_processor.py`).
8. **Embeddings**
   - The stored vector text for screenshot-derived contexts is typically: `title + summary`.
   - Embeddings are computed via the configured embedding endpoint (`opencontext/llm/global_embedding_client.py`).
9. **Vector DB upsert**
   - Stored into ChromaDB (default) or Qdrant (`config/config.yaml`, `opencontext/storage/unified_storage.py`).

### 4.3 Screenshot pipeline diagram

```mermaid
flowchart LR
  A[Saved screenshot file] --> B[POST /api/add_screenshot]
  B --> C[RawContextProperties\nsource=screenshot\ncontent_path=...]
  C --> D[ContextProcessorManager\nroutes to ScreenshotProcessor]
  D --> E[Resize (optional)]
  E --> F[pHash dedup]
  F --> G[Batch queue + background thread]
  G --> H[VLM call: screenshot_analyze\nreturns items[]]
  H --> I[Build ProcessedContext per item\n(title/summary/keywords/importance/confidence)]
  I --> J[LLM merge: screenshot_batch_merging\nmerge_type merged|new\nentities/tags/event_time]
  J --> K[Entity refresh\ncreate/update entity_context]
  K --> L[Embedding: Vectorize(text=title+summary)]
  L --> M[Vector DB upsert\nChroma/Qdrant]
```

---

## 5) Document ingestion pipeline (chunking applies)

Documents are ingested as `ContextSource.LOCAL_FILE`, `ContextSource.WEB_LINK`, or vault/monitor inputs, and processed by `DocumentProcessor` (`opencontext/context_processing/processor/document_processor.py`).

### 5.1 Supported file types

`DocumentProcessor.get_supported_formats()` includes:

- PDFs: `.pdf`
- Images: `.png .jpg .jpeg .gif .bmp .webp`
- Office: `.docx .doc .pptx .ppt .xlsx .xls`
- Structured data: `.csv .jsonl`
- Text/markdown: `.md .txt`

### 5.2 How document text is extracted

- **Structured files** (CSV/XLSX/FAQ): chunked by specialized structured chunkers (no VLM required unless you extend it).
- **Visual docs** (PDF/DOCX/PPT/images):
  - page-by-page analysis detects whether a page needs VLM (e.g., scanned pages or visual elements).
  - visual pages/images are sent through the VLM to extract text.

### 5.3 Chunking

Chunking is done by `DocumentTextChunker` (`opencontext/context_processing/chunker/document_text_chunker.py`):

- short docs (<10k chars): LLM-assisted semantic chunking using prompt `document_processing.text_chunking`.
- long docs: fallback chunking strategy.

### 5.4 Storage as `knowledge_context`

Each chunk becomes a `ProcessedContext` with:
- `context_type = knowledge_context`
- `summary = chunk.text`
- `vectorize.text = chunk.text`
- metadata `KnowledgeContextMetadata` including `knowledge_file_path`, `knowledge_raw_id`, etc. (`opencontext/models/context.py`)

### 5.5 Document pipeline diagram

```mermaid
flowchart LR
  A[Local file / Web link / Vault / Folder monitor] --> B[RawContextProperties\nsource=local_file/web_link/vault]
  B --> C[ContextProcessorManager\nroutes to DocumentProcessor]
  C --> D{Structured?\nCSV/XLSX/JSONL}
  D -- yes --> E[Structured chunker\n(FAQ/structured)]
  D -- no --> F{Visual?\nPDF/DOCX/PPT/Image/MD}
  F -- yes --> G[Page analysis + VLM on visual pages]
  F -- no --> H[Plain text extraction]
  E --> I[DocumentTextChunker\nchunk_text()]
  G --> I
  H --> I
  I --> J[ProcessedContext per chunk\ncontext_type=knowledge_context]
  J --> K[Embedding (chunk text)]
  K --> L[Vector DB upsert\nChroma/Qdrant]
```

---

## 6) Storage layer (Vector DB + SQLite)

### 6.1 Vector DB (RAG memory index)

Configured in `config/config.yaml`:
- default: **ChromaDB** local persistence (`opencontext/storage/backends/chromadb_backend.py`)
- option: **Qdrant** (`opencontext/storage/backends/qdrant_backend.py`)

The vector DB stores:
- `documents`: `ProcessedContext.vectorize.text`
- `embeddings`: `ProcessedContext.vectorize.vector`
- metadata: a flattened form of `extracted_data`, `properties`, and optional `metadata` (`opencontext/storage/backends/chromadb_backend.py`).

### 6.2 SQLite (app state)

SQLite stores:
- conversations/messages (chat UI)
- tips/todos/activities
- vault documents (daily reports, notes)
- and other app tables

---

## 7) Chat / RAG answering workflow (ContextAgent)

Chat entrypoints:
- Streaming/non-streaming agent chat: `POST /api/agent/chat` and `/api/agent/chat/stream` (`opencontext/server/routes/agent_chat.py`).

### 7.1 Workflow stages

The agent workflow is orchestrated by `WorkflowEngine` (`opencontext/context_consumption/context_agent/core/workflow.py`) with nodes:

1. **IntentNode**
   - classifies query into:
     - `simple_chat` (no retrieval)
     - `qa_analysis` (RAG answer)
     - `document_edit` (edit flow)
     - `content_generation` (generate flow)
2. **ContextNode**
   - uses an LLM prompt to plan **3–5 tool calls per round** and run up to 2 rounds.
3. **ExecutorNode**
   - uses a dedicated prompt to answer/edit/generate using the collected contexts.
4. **ReflectionNode**
   - exists but is currently not executed in the main workflow (commented out in code).

### 7.2 Tools used for retrieval

Tool definitions are in `opencontext/tools/tool_definitions.py`:

- Vector DB context retrieval tools:
  - activity / intent / semantic / procedural / state context retrieval
- SQLite retrieval tools:
  - daily reports / activities / tips / todos
- Entity tool:
  - profile + relationship traversal
- Web search tool:
  - optional external search (may require network access depending on runtime)

### 7.3 How “RAG” retrieval works

- If a retrieval tool is called with a `query`, the system:
  1. embeds the query text via the embedding model
  2. vector-searches the vector DB
  3. returns the stored `documents` text + metadata
- If a retrieval tool is called without a query, it can do metadata-only retrieval (e.g., time window).
- “Yesterday / last week” filtering is done via timestamp metadata fields like `event_time_ts`, `create_time_ts`, etc., passed as filters into the vector DB where clause.

Important: embeddings are **not** converted “back into text”. The vector DB stores both:
- the embedding vector (for similarity search)
- the original text document (returned to the agent and passed into the answer prompt)

### 7.4 RAG workflow diagram (backend)

```mermaid
flowchart TD
  A[User asks question\n/api/agent/chat] --> B[IntentNode\nclassify query]
  B -->|simple_chat| C[LLM reply only\n(no retrieval)]
  B -->|qa_analysis / generation / edit| D[ContextNode\nLLM plans tool calls]
  D --> E[Execute tools in parallel\n3-5 per round]
  E --> F{Tool type}
  F -->|Vector retrieval tool| G[Embed query text\nembedding_model]
  G --> H[Vector search\nChroma/Qdrant\n+ time/entity filters]
  H --> I[Return top-k contexts\n(text + metadata)]
  F -->|SQLite tool| J[Fetch reports/todos/tips/activities\nfrom SQLite]
  F -->|Entity tool| K[Entity resolution / relationships\n(entity_context)]
  F -->|Web search| L[External search results]
  I --> M[Tool-result filtering\n(LLM prompt)]
  J --> M
  K --> M
  L --> M
  M --> N[Collected context items]
  N --> O[ExecutorNode\nanswer/edit/generate\nwith collected_contexts]
  O --> P[Stream response + store messages\n(SQLite)]
```

### 7.5 How many “agents/tools” are involved?

- **Agent**: one `ContextAgent` with multiple nodes (intent/context/executor; reflection optional).
- **Tools**: typically **3–5 tool calls per iteration**, up to 2 iterations (config in `ContextNode`), so roughly 3–10 calls per user question depending on sufficiency.

### 7.6 Different intents → different execution paths

- `simple_chat`: direct response (no retrieval).
- `qa_analysis`: retrieve + answer.
- `document_edit`: retrieve + rewrite selected/document content.
- `content_generation`: retrieve + generate structured content.

---

## 8) Scheduled “proactive” features (not just Q&A)

Separate from chat, MineContext runs scheduled generation tasks (intervals in `config/config.yaml`), including:

- **Realtime activity monitor**: produces activity summaries and stores them in SQLite.
- **Daily report**: generates hourly summaries, then merges into a daily markdown report.
- **Todo generation**: extracts todos and stores todo embeddings for dedup in the vector DB.
- **Tip generation**: generates reminders based on recent activity patterns and context.

These features reuse:
- stored contexts from the vector DB
- activity/todo/tip/report tables in SQLite
- the same VLM client for generation prompts

---

## 9) What it does NOT do (current repo behavior)

- No native “video file” ingestion; screenshots are processed as images + timestamps.
- No dedicated “conversation summary → embedding → vector DB” memory pipeline (messages are stored in SQLite, but not embedded).
- No separate graph database. Entity “graph” is represented as relationship fields in entity metadata, traversed by `profile_entity_tool`.
- The `knowledge_context` chunks exist, but there is not (yet) a dedicated retrieval tool listed for `knowledge_context` in `opencontext/tools/tool_definitions.py`.

---

## 10) Extension points (common improvements)

- Add a retrieval tool for `knowledge_context` so document chunks participate in agent RAG by default.
- Add “re-run VLM on retrieved screenshot file paths” for pixel-level re-check at question time (currently answers rely on extracted text).
- Add conversation summarization + embedding storage (chat memory that becomes searchable like contexts).
- Turn on and integrate `processing.context_merger` (disabled by default) for broader long-term compression.

