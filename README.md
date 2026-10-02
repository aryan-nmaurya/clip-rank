# AI Shorts Creator

Two workflows for creating vertical reels from real footage:

- **Viral Clips:** upload a source video or paste a public video URL. Analyze motion and clarity, inspect sampled frames with a configured AI model, transcribe actual speech, and render distinct highlights with timed captions and original audio.
- **Ranking Video:** discover public short videos by topic, supply source URLs, or upload several clips. Rank their strongest moments and create a #N → #1 countdown with a bold title and persistent colored list, inspired by the supplied cat-ranking reference. Original audio is the default; brief narration is optional.

## Run

Install dependencies and build the interface:

```bash
.venv/bin/python -m pip install -r backend/requirements.txt
cd frontend
npm install
npm run build
cd ..
./run_app.sh
```

Open [the local app](http://localhost:8000). FFmpeg and FFprobe must be available on PATH. macOS `say` is used only when narration is enabled.

`run_app.sh` checks backend dependencies and rebuilds the frontend on each launch. Python 3.11–3.14 is supported by the installed dependencies; PyAV is bounded below 17 for compatibility with faster-whisper 1.2.1.

## Analysis and captions

Configure Gemini, OpenAI, or an **installed image-capable Ollama model** in Settings for frame-based semantic scoring. Auto uses a configured provider, then local AI, then measured visual metrics. An explicitly selected unavailable provider reports an error rather than switching to another provider. Provider readiness indicates configuration, not a successful authenticated request.

Without an AI model, clips are selected from measured motion and clarity. This fallback is identified in results. Retention scores are estimates; they do not promise views or virality. Source URLs, creators, chosen timestamps, scores, and reasons remain available with each result.

Real speech recognition uses faster-whisper's base model, downloaded into `storage/models` on first use. Captions are built from recognized word timestamps and rendered with Pillow as a transparent video track, so FFmpeg does not need libass. Missing speech or unavailable transcription produces no invented captions. Silent videos can still render.

## Source and export behavior

- Public URLs use yt-dlp to download actual footage. Failed/private/unavailable downloads produce actionable errors; there is no demo-footage fallback.
- Automatic discovery searches YouTube Shorts and excludes existing ranking compilations. At least N distinct usable sources are required for Top N.
- Ranking entries play from the lowest selected score (#N) to the highest (#1). A complete 3–12-second moment is selected for each source; the editor does not repeat footage to fill time.
- Viral clips stay within the source's duration. Short videos may yield fewer distinct clips than requested.
- Framing can preserve the full image over a blurred background or fill the frame with a center crop. Center cropping is not subject tracking; use full-frame mode when important action is near an edge.
- Exports contain 720×1280 H.264 video at 30 fps and AAC audio, including a silent audio track for silent inputs. Exports are duration-checked and decoded before becoming ready.
- Media work runs outside the API loop. Up to two jobs run concurrently, with live progress and cooperative cancellation of FFmpeg work.
- Uploaded sources are kept under `storage/projects` so regeneration works. Downloaded sources and temporary files are removed after each job. Finished MP4s and previews are kept under `storage/output`.

Only process footage you have permission to use. Public availability does not grant reuse rights.

## Verify

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
cd frontend && npm run build
```

Tests use isolated databases and explicit test footage. They verify source validation, real rendering, ranking order, bounds, caption timing, narration mixing, silent-video support, cancellation, and API responsiveness.

The app API lives in `backend/app/api`, pipelines in `backend/app/pipelines`, and media/analysis helpers in `backend/app/media`. Existing design documents in `Docs` describe earlier plans; this README documents the current implementation.
