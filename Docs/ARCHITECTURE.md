# AI Shorts Creator — Architecture Specification

## 1. Architecture Plan
The application is an autonomous AI video creator built on a clean three-tier architecture:
- **Presentation Layer (Frontend)**: React 18, TypeScript, Tailwind CSS, Lucide icons, Vite. Clean, modern, distraction-free desktop-first UI with two primary creator flows: **Viral Clips** and **Ranking Videos**. Real-time updates delivered via Server-Sent Events (SSE).
- **Application & Orchestration Layer (Backend)**: Python FastAPI with an asynchronous Job Queue engine, AI Router, and Pipeline Orchestrator. Exposes REST endpoints for projects and jobs, plus SSE streaming for live progress.
- **Media & Intelligence Core**: Local FFmpeg processing engine, scene detector, frame extraction, transcription abstraction, reframing engine (9:16 vertical), ranking engine, viral scoring engine, and TTS synthesizer.
- **Storage & Lifecycle Management**: SQLite database with WAL mode for persistent state; job-scoped isolated temporary storage (`storage/temp/<job_id>`) with automated guaranteed cleanup.

---

## 2. Directory Structure
```text
Ranking-Video/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── VIRAL_PIPELINE.md
│   └── RANKING_PIPELINE.md
├── backend/
│   ├── app/
│   │   ├── core/
│   │   │   ├── config.py             # Environment configuration & safety limits
│   │   │   ├── database.py           # SQLite persistence layer
│   │   │   ├── queue.py              # Asynchronous job queue & worker pool
│   │   │   └── events.py             # SSE event broadcasting
│   │   ├── models/
│   │   │   ├── project.py            # Project, Job, CandidateMoment, Clip schemas
│   │   │   └── settings.py           # Provider and rendering configurations
│   │   ├── ai/
│   │   │   ├── base.py               # AIProvider interface
│   │   │   ├── router.py             # Central AI task router
│   │   │   ├── gemini.py             # Google AI Studio provider
│   │   │   ├── openai_provider.py    # OpenAI provider
│   │   │   ├── local_provider.py     # Local Ollama provider
│   │   │   └── fallback.py           # Deterministic offline provider
│   │   ├── media/
│   │   │   ├── ffmpeg_core.py        # Safe, structured FFmpeg process abstraction
│   │   │   ├── scenes.py             # Fast local scene detection
│   │   │   ├── frames.py             # Adaptive frame extraction
│   │   │   ├── reframer.py           # 9:16 vertical reframing with cinematic blur
│   │   │   └── captions.py           # Dynamic word/phrase highlighted captions
│   │   ├── transcription/
│   │   │   └── transcriber.py        # Audio extraction & timestamped transcription
│   │   ├── tts/
│   │   │   └── voice_engine.py       # Pluggable TTS synthesizer
│   │   ├── sources/
│   │   │   ├── discovery.py          # Query generator & permitted source search
│   │   │   └── ingestion.py          # Video ingestion & local media validator
│   │   ├── pipelines/
│   │   │   ├── ranking/
│   │   │   │   ├── ranking_pipeline.py # End-to-end Ranking workflow
│   │   │   │   ├── deduplication.py    # Perceptual & timestamp deduplication
│   │   │   │   ├── scorer.py           # Multi-factor ranking scoring
│   │   │   │   └── script_builder.py   # Retention-optimized hook & countdown
│   │   │   └── viral/
│   │   │       ├── viral_pipeline.py   # End-to-end Viral clips workflow
│   │   │       ├── viral_scorer.py     # Viral coefficient calculation
│   │   │       └── clip_selector.py    # Boundary & context optimization
│   │   ├── storage/
│   │   │   └── manager.py            # Job-scoped storage & guaranteed cleanup
│   │   └── api/
│   │       ├── routes.py             # REST API & SSE endpoints
│   │       └── server.py             # FastAPI application definition
│   ├── tests/
│   │   ├── test_ranking_pipeline.py  # E2E test for ranking workflow
│   │   ├── test_viral_pipeline.py    # E2E test for viral workflow
│   │   └── test_api.py               # API & SSE test suite
│   ├── requirements.txt
│   └── run.py
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Header.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   ├── VideoModal.tsx
│   │   │   └── ProgressStages.tsx
│   │   ├── pages/
│   │   │   ├── HomePage.tsx          # Dual-choice creator hub
│   │   │   ├── ViralPage.tsx         # Viral Clips intent input
│   │   │   ├── RankingPage.tsx       # Ranking Video intent input
│   │   │   ├── JobProgressPage.tsx   # Live generation & results preview
│   │   │   ├── LibraryPage.tsx       # All projects & clips
│   │   │   └── SettingsPage.tsx      # Provider & hardware configuration
│   │   ├── api.ts
│   │   ├── types.ts
│   │   └── App.tsx
│   └── package.json
└── run_app.sh
```

---

## 3. Data Models
- **Project**:
  - `id`: unique string (`proj_...`)
  - `mode`: `"viral"` | `"ranking"`
  - `title`: human readable title
  - `status`: `"CREATED"` | `"PROCESSING"` | `"COMPLETED"` | `"FAILED"` | `"CANCELLED"`
  - `input_data`: JSON (URL, uploaded file, topic, count, provider preferences)
  - `result_data`: JSON (generated clips, scripts, rankings, durations)
  - `created_at`, `updated_at`: ISO timestamps
- **Job**:
  - `id`: unique string (`job_...`)
  - `project_id`: foreign reference
  - `status`: job stage state machine
  - `progress`: 0 to 100 percentage
  - `current_stage`: human readable label (e.g., "Finding best moments", "Rendering")
  - `error_message`: safe user-facing error
  - `detailed_error`: debug diagnostics
- **CandidateMoment**:
  - `id`, `source_id`, `start`, `end`, `description`
  - `visual_impact`, `surprise`, `retention`, `clarity`, `overall_score`
- **OutputClip**:
  - `id`, `project_id`, `job_id`, `title`, `duration`
  - `viral_score` (for viral clips) or `rank` (for ranking video)
  - `video_path`, `preview_path`, `status`

---

## 4. Job Lifecycle & State Machine
```text
QUEUED → INGESTING → TRANSCRIBING → ANALYZING → DETECTING_MOMENTS → DEDUPLICATING
  → RANKING / SCORING → SCRIPTING → GENERATING_VOICE → EDITING → RENDERING
  → CLEANING → COMPLETED (or FAILED / CANCELLED)
```
- Each state transition emits an SSE event to connected frontends.
- Cancellation flags abort subprocesses and immediately invoke cleanup.

---

## 5. Media Pipeline
- **Input Ingestion**: Local uploads or permitted remote URLs sanitized into MP4 format.
- **Audio Extraction**: High-fidelity 16kHz mono WAV extraction for transcription and speech energy profiling.
- **Scene Detection**: Keyframe changes detected locally via FFmpeg filter `select='gt(scene,0.35)'` or adaptive segmenting.
- **Reframing Engine**: 9:16 vertical canvas (720x1280 or 1080x1920) created using a dual-stream filtergraph: blurred zoomed background + center-fitted foreground with aspect preservation.
- **Captions & Overlays**: Clean, word/phrase grouped captions rendered with high-contrast text and drop shadows; prominent countdown badges (`#5` down to `#1`) rendered with rank-specific color coding.

---

## 6. AI Provider Architecture
- `AIProvider` abstract base class defining `generate_text`, `analyze_images`, and `generate_structured`.
- `AIRouter`: evaluates task complexity, provider availability, and user preferences:
  - `Auto`: uses Local AI for simple classification and script cleanup; escalates to Gemini / OpenAI for multimodal reasoning; falls back to deterministic procedural engine on network or quota failure.
  - `Google AI Studio`: direct Gemini 2.5 Flash / Gemini 1.5 Flash integration.
  - `OpenAI`: GPT-4o / GPT-4o-mini integration.
  - `Local`: Ollama on `http://localhost:11434` (`qwen2.5`).
  - `Fallback`: 100% offline heuristic generator ensuring zero user interruptions.

---

## 7. Ranking Pipeline (Workflow B)
1. **Topic Parsing & Count Extraction**: parses topic and extracts count (e.g., *"Top 7 Football Saves"* → topic: "Football Saves", count: 7; default: 5).
2. **Search Query Generation**: generates diverse visual search queries.
3. **Source Discovery & Ingestion**: discovers permitted candidate videos.
4. **Candidate Moment Detection**: extracts moments with scene boundary detection and visual motion energy.
5. **Deduplication**: filters duplicate moments using duration, similarity, and perceptual checks.
6. **Ranking & Progression**: ranks moments from #5 down to #1, ordering for escalating viewer engagement.
7. **Retention Script & Voice**: generates curiosity hook, concise moment setups, bridges, and TTS audio.
8. **Composition & Render**: composites vertical clips with ranking badges, captions, and transitions.
9. **Cleanup**: purges temporary files; preserves final MP4 and preview.

---

## 8. Viral Pipeline (Workflow A)
1. **Ingestion & Validation**: validates source video URL or uploaded file.
2. **Transcription**: extracts timestamps, spoken keywords, and sentence boundaries.
3. **Scene & Energy Detection**: analyzes speech spikes, audio excitement, and visual scene cuts.
4. **Viral Potential Scoring**: evaluates hook strength, emotion, surprise, information density, and visual activity (0–100 score).
5. **Clip Selection & Context Padding**: identifies optimal clip boundaries with complete setup → action → payoff arcs (15–60s).
6. **Vertical 9:16 Reframing**: converts footage into 9:16 aspect ratio with cinematic background blur.
7. **Captions & Hook Overlays**: synchronizes bold, readable caption phrases.
8. **Render & Export**: outputs individual viral clips with score badges, previews, and download links.

---

## 9. Storage Lifecycle & Cleanup Policy
- `storage/temp/<job_id>/`: sandboxed workspace for all intermediate files (audio clips, frames, raw scenes, render caches).
- **Guaranteed Cleanup**: executed in a `finally` block upon job completion, failure, or cancellation.
- **Startup Scanner**: cleans any orphaned temporary directories older than `TEMP_RETENTION_HOURS` (12h) to prevent disk accumulation.
- `storage/output/`: permanently stores final MP4 files and preview thumbnails.

---

## 10. Implementation Plan
- **Phase 1**: Core foundation — storage manager, database, AI router & providers, FFmpeg abstraction, asynchronous worker engine.
- **Phase 2**: Viral clips pipeline — ingestion, transcription, viral scoring, 9:16 reframer, captioning, clip renderer.
- **Phase 3**: Ranking video pipeline — topic parser, source discovery, candidate analysis, ranking scorer, countdown overlays, TTS, renderer.
- **Phase 4**: Frontend UI — Home hub (Dual Choice), Viral mode screen, Ranking mode screen, real-time Job Progress screen, Library & Settings.
- **Phase 5**: Verification & Automated Testing — E2E tests for both workflows, API validation, and production packaging.
