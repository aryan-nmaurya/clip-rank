# ClipRank production guide

What a finished Short is, how ClipRank proves it, and what stops a bad one from leaving the machine.
Autopilot scheduling, the cloud control plane and deployment are documented in `AUTONOMOUS_STUDIO.md` and are not
changed by this guide.

## The delivery spec (enforced on the file, not the plan)

`backend/app/media/verify.py::verify_mp4` is the single definition. It inspects the bytes with FFprobe and a full decode,
and reports **every** violated rule:

| Rule | Required |
| --- | --- |
| Container streams | exactly one video, one audio, nothing else (no subtitle/data tracks) |
| Video | 1080×1920 (9:16), H.264, `yuv420p`, 30 fps, decodable frames that match the duration |
| Audio | AAC, 48 kHz, stereo |
| Duration | 10.1 s to 180 s, and within 3 % of the planned edit |
| File | non-empty, decodes with `-xerror` |

`StorageManager.move_final_clip` calls it before any file reaches `storage/output`, so no engine (Ranking, Viral Clips,
Movie, Studio) can store a non-conforming MP4. A render is *complete* only when this passes **and** `ProductionQC.inspect`
(black/frozen frames, clipping, narration audible, caption timing, retention) **and** the multimodal final review pass.

### Retention checks (`media/retention.py`)
Filler openings ("Hey guys, welcome back…", "Today we're going to…", "Like and subscribe") fail QC, as does more than 4 s of
dead air or a single countdown beat longer than 20 s. Late first speech is reported as a warning. These are measurements
on the finished MP4, not predictions of views.

## Publish gates (all three must pass)

`publishing/gates.py`, applied inside `youtube.queue_upload`, exposed at `GET /api/clips/{id}/gates`.

* **TECHNICAL** — `verify_mp4` on the stored file.
* **RIGHTS** — fail closed. A source passes only with a documented reusable licence (Creative Commons Attribution with the
  source URL; non-commercial / no-derivatives never pass) **or** a rights statement the channel owner recorded
  (`POST /api/projects/{id}/rights`) naming that exact footage. The statement is bound to the sources' identity keys, so a
  regenerated project using different footage is *not* covered. "Downloadable" is never treated as "reusable". Attested
  rights are labelled `ATTESTED` (owner-asserted) versus `PASS` (documented). Movies keep their existing generation-time
  rights policy.
* **ORIGINALITY** — must contain meaningful original editorial work (ranking structure + original narration + hook +
  reviewed edit). A caption-and-crop of someone else's clip fails and says so.

Provenance (URL, creator, licence, acquisition time, usage basis) is stored on every project in `result_data.sources`.

## When something fails

Every failed job stores a structured record (`jobs.failure`, returned by `GET /api/jobs/{id}`):
**what** failed, **why**, whether it **can retry**, and what to do **next**, plus the stage it died in and a redacted
traceback. `core/failures.py` classifies FFmpeg errors (with exit code and stderr), source sign-in walls, thin source pools,
AI rate limits, disk exhaustion, QC rejections and more. API keys, bearer tokens and OAuth secrets are redacted from stored
errors and from every log line (`install_log_redaction`). Terminal job states are absorbing (`core/states.py`): a late
progress write cannot resurrect a cancelled or failed job.

## Is this machine able to make a Short? (`GET /api/health`, Diagnostics dialog, Home banner)

Checks FFmpeg/FFprobe, H.264/AAC encoders and the audio filters ClipRank uses, Python packages, the Whisper model, the
narration voice, free disk, the database, writable output directories and AI credentials. It answers `READY` or names the
exact problem and the fix.

**Production test** (`POST /api/diagnostics/production-test`, or
`PYTHONPATH=backend .venv/bin/python -m app.core.production_test`) generates a ClipRank-original test asset (so there is no
rights question), then runs ingestion → transcription → AI analysis → narration → captions → render → QC and writes
`storage/output/diagnostics/cliprank-production-test.mp4`. It reports `SYSTEM READY` or the failing component. In the last
verified run on this machine all seven stages passed in ~30 s.

## Storage

`GET /api/storage` / Diagnostics dialog show what ClipRank uses. Abandoned workspaces are reclaimed at startup and after every
job; working files are held to a budget (`CLIPRANK_TEMP_BUDGET_GB`, default 20) and a free-space floor
(`CLIPRANK_MIN_FREE_GB`, default 5). Running jobs, unfinished studio tasks and recent failures (kept for diagnosis until the
retention window ends) are never touched; deletion is confined to `storage/temp` and never follows symlinks. Each ranking job
also stops if its own workspace exceeds its quota.

## AI quota and key failover

Settings accepts **two Google AI Studio keys** (both kept in the OS keychain, never returned by the API). When a key's daily
quota is spent, requests switch to the other key automatically; the spent key is remembered process-wide until its reset time.
Only when every key is spent does a job stop, with a clear `AI_QUOTA` failure ("retry in about 11h 46m… or switch provider")
instead of marking good footage as unverifiable. Per-minute limits wait the delay the API asks for. Free-tier quotas are per
Google project, so create the second key in a **different project**. Sources the model has genuinely judged unsuitable for a
topic are remembered for 30 days, so retries don't pay to re-judge them.

## Providers and failover order

In **Auto**, ClipRank tries providers best-first: local Ollama, **Gemini** (every key, then every fallback model), **Groq**,
**NVIDIA NIM**, then OpenAI, moving on whenever one is out of quota or cannot answer. Inside Gemini a key is skipped on a daily
quota stop, on a rate limit (another key takes the request immediately) and when the key is rejected as invalid (skipped on every
model). Groq and NIM speak the OpenAI-style API, review sampled frames rather than whole videos, and stack extra images into
montages to respect their per-request image limits. Keys are kept in the OS keychain. Whole-video review is used only while a
Gemini key still has quota; afterwards the frame-based review runs. Settings → Vision connections shows how many Gemini keys
are usable right now.

## Retry resumes from a checkpoint (Ranking)

Progress is saved under `storage/projects/<project>/resume/` as it happens: every downloaded source (hard-linked, so no
extra disk), which ones were already judged, the clips that verified, the rejections, and - once enough have verified - the
approved pool. A quota stop, crash or restart therefore costs nothing already paid for: **Retry** continues with the sources
that were still waiting and carries on, or goes straight to scripting/rendering if the pool was already approved. It lives
outside the job's temporary workspace, so the 12-hour cleanup cannot remove it before a daily quota resets. A checkpoint is
only reused for the same topic, count, version setting and footage mode; it expires after 7 days and is removed on success.
**Start over** discards it. Viral Clips and Movie keep their own behaviour (Movie already has a checkpoint; Viral Clips
restarts, which re-runs only cheap local steps before its AI planning).

## Ranking Videos

* **One Short by default.** The earlier A/B pair (two Shorts from fully disjoint footage) is still available (`variants: 2`)
  but needs `2×N` verified sources; a single Short needs `N`.
* **Discovery is modular.** `sources/providers.py` normalizes every provider (YouTube via yt-dlp + Shorts search, Reddit,
  Dailymotion, Vimeo, web search, RSS, local, licensed) to one candidate schema (`id, source, title, url, creator,
  published_at, duration, engagement_signals, description, rights_status, source_metadata`) and labels each candidate
  TRENDING / ESTABLISHED / EMERGING. Reach never selects a clip; it only records why it was considered.
* **Queries target the event, not ranking pages** ("Parkour save caught on camera", not "Craziest parkour saves"), and
  pre-download filters drop compilations by duration, game/animation/tutorial titles, and existing rankings.
* **The search widens** (up to 3 rounds, 36 candidates) with three sources verified concurrently, instead of failing on the
  first thin batch. Verification strictness is unchanged: each source passes screening, topic verification, a blind
  observation and a final-cut review before it can be ranked.

## Business layer (`/api/business/*`, Analytics page)

Only recorded numbers are shown. Revenue, sponsorship and costs come from the ledger (manual entries with a stated source) or
from YouTube's APIs; cloud AI usage is counted from real Gemini token counts and converted to money only with prices you
enter. Net = recorded revenue − recorded costs. Monetization status tracks YouTube's published thresholds; nothing is
projected. Analytics are stored per reporting window (early / 24 h / 72 h / 7 d) with the creative choices behind each video.

The learning analyzer needs ≥ 8 measured videos and ≥ 3 per pattern, uses medians with outlier capping and shrinkage toward
the channel baseline, bounds each weight to ±30 %, and stores the numbers behind every adjustment. It produces advisory
weights; it does not rewrite strategy.

Affiliate links are added to a description only when the Short's own content mentions one of the program's topics, always
with your disclosure text.

## Not covered here (by design or not yet built)

* Autopilot scheduling, emergency-stop controls, the 7-day stability run, the cloud control plane and the worker/lease split
  were excluded from this pass.
* Experiments (A/B testing of one variable) need Autopilot to apply the variants, so they are not implemented.
* Subject-tracking reframing exists for Movie moments only; Ranking and Viral Clips use the full frame over a blurred
  background (nothing important is cropped out).
* Optional AI-generated story video is not implemented.
