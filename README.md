# ClipRank

The local-first autonomous studio adds **Viral Discovery** and **Autopilot** for an **Extreme, Unbelievable & Funny Moments** channel. See [studio setup and deployment status](Docs/AUTONOMOUS_STUDIO.md). The Vercel Hobby + Supabase control plane is prepared in `cloud/`; video compute stays on the Mac. Hobby permits personal, non-commercial use only.

Creation workflows for vertical reels from real footage:

- **Viral Clips:** upload a source video or paste a public video URL. Find complete standalone stories using connected vision, preserve actual dialogue, remove filler without cutting midword, and render finished highlights with timed captions and mixed original audio. Silent action footage receives grounded neural narration.
- **Ranking Video:** discover individual videos across selected sites, supply public source URLs, or upload several raw clips. Screen out existing rankings before extracting moments and produce a **#N → #1 Short** (or, optionally, **two distinct Shorts**: A favors fast entertainment, B favors suspense, built from completely separate verified sources). Discovery widens its search in rounds until enough sources verify. Every Short requires natural neural commentary, timed captions, safe rank graphics and finished-video QC.
- **Movie / Trailer Moments:** provide an authorized movie/trailer URL or upload. Analyze the full source locally, then verify selected moments with connected vision. Automatically preserve strong actor dialogue, add brief commentary only when useful, or create cinematic edits with authorized original audio or documented licensed music. Finished 1080×1920 clips, provenance and QC metadata appear in the Library. See [movie mode](Docs/movie_moments.md) for setup and current English speech support.

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

Open [the local app](http://localhost:8000). FFmpeg and FFprobe must be available on PATH. Default narration uses [Pocket TTS](https://github.com/kyutai-labs/pocket-tts), integrated into the existing backend and running on CPU. Explicitly install local voice assets with `PYTHONPATH=backend .venv/bin/python backend/setup_pocket_tts.py`. Preview Alba, Marius or Javert in Ranking or Settings. Online Edge voices and optional Kokoro remain available. Pocket TTS generation stays offline after setup and fails clearly if assets or accurate word timings are missing.

Both production workflows require a connected image-capable model. macOS Vision OCR (requires Command Line Tools), or Tesseract on other platforms, adds ranking-overlay screening. The macOS OCR helper compiles into ignored `storage/tools` on first use. OCR and motion measurements alone cannot verify a requested topic.

`run_app.sh` checks backend dependencies and rebuilds the frontend on each launch. Python 3.11–3.14 is supported by the installed dependencies; PyAV is bounded below 17 for compatibility with faster-whisper 1.2.1.

## Analysis and captions

Connect Gemini or an **installed image-capable Ollama model** in Settings. Enter the Gemini key/model (default `gemini-3.1-flash-lite`), or your Ollama server base URL/model name, use **Test vision connection**, then save. The test checks an actual image. The local connection check reads `/api/tags` and `/api/show` and requires `vision` capability. It never pulls or installs a model. The default local model field is [`qwen3-vl:4b`](https://ollama.com/library/qwen3-vl:4b); replace it with the exact name of your installed vision model. Setup can be saved before either service is connected.

Auto checks connected local vision first, then configured cloud providers. OpenAI remains supported as an additional provider. Cloud key readiness indicates configuration, not a successful authenticated request; use the image test for Gemini. An explicitly selected unavailable provider reports an error.

**Ranking has no motion-only fallback.** It stops before downloading sources if no vision engine is connected. Candidate cuts must explicitly match the requested subject, action and outcome with at least 0.85 model relevance/confidence and a complete visible event without graphic injury close-ups. Missing fields, uncertain decisions and omitted candidates are rejected. A second review checks the exact cut with its chosen framing, label and commentary before export. For “Parkour fails,” a successful stunt or preparation alone cannot qualify. Descriptions come from frames, not uploader titles. These checks reduce mismatch; model judgments can still be wrong, so inspect the export.

Viral Clips also require vision to verify a complete standalone story. Motion and clarity propose windows; they cannot approve production exports. Retention scores and model confidence are estimates, not guarantees of accuracy, views or virality. Source URLs, creators, timestamps, visible evidence, scores and reasons remain available with results.

Real speech recognition uses faster-whisper's base model, downloaded into `storage/models` on first use. Captions are built from recognized word timestamps and rendered with Pillow as a transparent video track, so FFmpeg does not need libass. Speech without actual word timing is rejected rather than given estimated captions. Unavailable transcription stops production. Pocket TTS captions use measured local Whisper timings; online narration uses speech-service word boundaries. Speech stays at natural speed and lines are rewritten when they do not fit.

## Source and export behavior

- Public URLs use yt-dlp to download actual footage. Failed/private/unavailable downloads produce actionable errors; there is no demo-footage fallback.
- Automatic discovery searches YouTube and the public Dailymotion video API, with indexed-video search for Reddit, TikTok, Instagram, and Vimeo. Select sites in the Ranking page. These sites may block search/downloads or require sign-in; failures are reported and public links/uploads remain available. A reel need not contain every selected site if usable clips are unavailable there.
- Every ranking input, including pasted links and uploads, is checked for ranking/countdown/compilation titles and tags. Source-wide sampled frames are OCR-screened for numbered lists and changing countdown badges, and the connected vision model checks for rankings and unrelated montages. Rejected sources and reasons appear with results. At least 2N different unused sources must pass topic, source and final-cut screening; rejected footage never fills an empty rank.
- Every new finished Short must exceed 10 seconds (minimum 10.1 seconds). The check runs on both the edit and the encoded MP4; short footage is never looped, frozen or padded to qualify. A/B ranking selections are disjoint, and a persistent source-use ledger blocks previously used videos and overlapping standalone moments across later jobs, including regeneration.
- Ranking edits retain the complete independently verified ending and aftermath. Connected video-capable vision also watches the actual source cut before narration; estimated event timestamps never shorten its ending.
- Commentary builds curiosity from verified visible setup without announcing the payoff early. The first line is an immediate topic hook; later lines vary their bridges. Titles from source uploads never become ranking descriptions. Source audio ducks smoothly during narration and returns afterward. Original rank-reveal effects are used selectively; no background music is added. Source credits, topic evidence, scripts and actual speech timings are retained.
- Verified payoffs are reviewed together to establish distinct comparative scores with visible evidence, rather than reusing unrelated per-clip ratings. Ranking entries play from the lowest selected score (#N) to the highest (#1). A complete 3–12-second moment is selected for each source; the editor does not repeat footage to fill time.
- Viral clips stay within the source's duration. Short videos may yield fewer distinct clips than requested.
- Production preserves the complete source frame over a blurred background so action near an edge remains visible. Safe caption groups contain up to three words with selective keyword emphasis. This implementation does not perform subject tracking.
- Exports contain **1080×1920 H.264 video at 30 fps and AAC stereo audio at 48 kHz**. The mix is mastered toward −14 LUFS with peak headroom. Mandatory final QC decodes the MP4, checks codecs/dimensions/duration, unexpected black or frozen sections, audio levels and actual word timing. Gemini reviews a proxy made from the finished video with its audio and must describe time-stamped evidence for every segment; local vision reviews final sampled frames with separate objective audio checks. Neither ranking video appears Ready until both pass. Repair is bounded to three attempts; an approved A/B variant is retained while its partner is repaired. A sentence that fails speech verification must be rewritten instead of resynthesizing identical wording indefinitely. A rejected result is withheld. Older exports without these checks cannot be downloaded or uploaded.
- Media work runs outside the API loop. Up to two jobs run concurrently, with live progress and cooperative cancellation of FFmpeg work.
- Uploaded sources are kept under `storage/projects` so regeneration works. Successful jobs delete their managed intermediates only after final files are verified and safely copied to `storage/output`. Failed jobs retain their workspace for diagnosis; startup clears abandoned directories after the configured retention period (12 hours by default). Cleanup cannot escape managed job storage or follow an outside symlink. Finished MP4s/previews and lightweight scripts, ranking metadata and provenance remain.

Only process footage you have permission to use. Public availability does not grant reuse rights.

## Production hardening

* **Is this machine ready?** Open the **AI Engine** badge → *System readiness* and **Run production test**. It makes a real Short from a generated test asset and reports `SYSTEM READY` or the exact failing component. Same check from the shell: `PYTHONPATH=backend .venv/bin/python -m app.core.production_test`.
* **Delivery spec.** Every stored MP4 is verified with FFprobe + a full decode: one video and one audio stream only, 1080×1920 H.264 `yuv420p` at 30 fps, AAC 48 kHz stereo, 10.1–180 s.
* **Publish gates.** TECHNICAL, RIGHTS and ORIGINALITY must all pass before YouTube upload. Rights fail closed: documented licences (e.g. CC BY) pass; otherwise record your rights basis for the exact sources in the clip's upload panel.
* **Failures explain themselves** (what / why / can it retry / next step), with secrets redacted.
* **Analytics** page: recorded revenue and costs only, YouTube results by reporting window, and a conservative learning analyzer.

Details and limits are in [Docs/PRODUCTION.md](Docs/PRODUCTION.md).

## Direct YouTube upload

Finished clip cards have **Upload to YouTube**. Once your channel is connected, that button transfers the actual MP4 privately from the backend, without navigating away. ClipRank monitors YouTube processing and rejection status, then applies the selected visibility automatically after the actual Studio copyright verdict is confirmed. The standard API does not expose that verdict; processing success is never treated as copyright clearance. Autopilot productions also retain their daily selection, schedule and spacing gates. Publication failures stay private and are retried. Progress, errors, retry and the completed video link appear on the same card. Expand **Upload details** to change the title, description, visibility or audience before uploading. The default target is Public and Not made for kids; set the appropriate audience for your content. Descriptions start with 13 lines containing one period each, followed by creator and voice/license credits without links, then a summary of what happens in that finished Short. Ranking summaries use the uploaded variant's own moments and rank order. Each upload includes 32 relevant YouTube keyword tags, within the API's 500-character budget including commas and implicit multiword quotes. Tags use the observed action rather than an unverified search topic. Source URLs remain in internal provenance. Final description and tag previews are available before upload; retries preserve the original metadata. Existing uploads can receive updated descriptions and tags without changing visibility.

One-time setup in Settings → **YouTube direct upload**:

1. Enable [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com) in your Google Cloud project.
2. Configure OAuth consent and, if your app is in Testing, add your Google account as a test user.
3. Create an OAuth client with application type **Desktop app**. Download its client JSON and choose it in Settings.
4. Click **Connect YouTube** and complete Google authorization in the sign-in tab. The callback is `http://127.0.0.1:8000/api/youtube/callback`. Choose the account/channel you want to upload to. This initial authorization is the only browser navigation; uploading does not redirect.
5. Save your upload visibility and audience defaults. Each QC-approved ready clip can then be uploaded in one click.

Authorization uses OAuth state, PKCE and upload/read-only channel scopes. Client credentials and refresh tokens stay in ignored `backend/data/youtube_connection.json` with owner-only file permissions; APIs never return tokens. The YouTube routes accept connections only from this computer. Disconnect removes local channel tokens; Google account permissions can also be revoked in your Google account settings.

Transfers use [YouTube's resumable upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol) in bounded chunks. Repeated clicks reuse the clip's upload record. An interrupted transfer or backend restart can be resumed with **Retry upload**; the app checks Google's existing session before sending remaining bytes and retains the original metadata/channel. Keep the MP4 unchanged until upload completes. If Google expires the session, the app reports that and requires another explicit retry to start a new session.

[Google restricts uploads from unverified API projects to Private](https://developers.google.com/youtube/v3/docs/videos/insert); public uploads can require an API audit. The result shows the visibility actually returned by YouTube. Quota exhaustion, revoked authorization and missing channels are reported in the app. A successful transfer does not mean YouTube has finished processing the video. No live account upload is performed by the test suite.

## Verify

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
cd frontend && npm run build
```

Tests use isolated databases and explicit test footage. They verify source validation, real rendering, ranking order, topic/label rejection, strict vision responses, setup without model downloads, bounds, exact captions, distinct A/B scripts and source selection, preserved payoff boundaries, narration mixing, rejected black/frozen outputs, native stereo peak checks, cleanup containment, cancellation and API responsiveness. YouTube tests mock Google responses and verify OAuth state/PKCE, token refresh/storage, metadata/credits, chunk positions, duplicate clicks, lost-response recovery, restart recovery and local access restrictions. Vision decisions in rendering tests are explicitly mocked; live semantic accuracy must be verified after connecting your model.

The normal launcher binds to this computer only and does not restart running jobs when source files change.

The app API lives in `backend/app/api`, pipelines in `backend/app/pipelines`, and media/analysis helpers in `backend/app/media`. Existing design documents in `Docs` describe earlier plans; this README documents the current implementation.

Generated media, local databases, model weights, dependency folders and archives are ignored by Git. Ignoring an already committed file does not remove it from older commits; use a reviewed history migration if a remote still rejects historical large objects.
