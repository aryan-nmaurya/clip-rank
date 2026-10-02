# ClipRank local studio and cloud setup

The default channel is **EXTREME, UNBELIEVABLE & FUNNY MOMENTS**. The promise is a surprising, impressive, funny, intense or unbelievable payoff in every Short. Autopilot starts paused, with automatic publishing off. No model downloads, OAuth consent, cloud deployment, or login service installation happen automatically.

## Run locally

Run `./run_app.sh` and open `http://127.0.0.1:8000`. The local API includes a worker. Alternatively, use `PYTHONPATH=backend .venv/bin/python backend/worker_cli.py run` for a standalone worker. Durable leases prevent two workers from claiming the same production.

Viral Discovery is the central autonomous engine. It searches individual footage across YouTube, Reddit and Dailymotion in ten related visual-entertainment pillars: insane sports, parkour/freerunning, human skills, epic saves, physical fails, trick shots, near misses, crazy stunts, unexpected recoveries and satisfying physical moments. Known sources, existing rankings and compilations are excluded. View counts do not drive the editorial score.

Discovery searches up to 30 opportunities, deeply inspects up to 10 with connected vision, verifies complete topic-matching moments and independently reviews the proposed cut. It rates hook strength, visual payoff, surprise, emotion, clarity, retention, replay value, shareability, original commentary potential and audience fit. Unclear first-second interest is rejected. These are editorial ratings, never viral probabilities. Discovery uses a bounded 40-call cloud budget and cleans its managed temporary workspace.

ContentDirector selects up to five finalists and produces up to three candidates. One exceptional standalone moment selects `viral_clip`; at least five distinct verified moments on the same topic can select `ranking`; useful visible context selects `commentary`. Rankings create two distinct A/B videos through the existing real-footage renderer. Standalone modes stay inside the discovered source window. All autonomous modes require original grounded narration, timed captions, source audio mixing and actual finished-video review. No random cross-niche compilations or quota fill. Weak days may produce or publish nothing.

The publication target is up to two high-quality Shorts per day, with an exceptional third allowed by the configured threshold. Private copyright-check uploads are separate from public releases. Public releases have a six-hour spacing reservation across workers.

### Rights and copyright before publication

The selected default policy is `user_managed`: unknown-rights footage can enter production, with its unknown license and source provenance retained. This records the channel owner's rights decision; it does not label that footage licensed. `documented_permission` is also available and requires source-specific commercial permission/license evidence in the profile's `source_rights` entries. Known incompatible licenses are rejected. Original narration does not imply ownership of third-party footage.

All new YouTube transfers start private, even when Public or Unlisted is selected. ClipRank reads the actual upload's processing status and explicit rejection reasons. Processing success does **not** pass copyright review. The standard YouTube Data API does not expose the complete Studio Content ID Checks verdict. The channel owner must review that actual result and record a clear verdict/evidence note in ClipRank; otherwise the video waits privately. This is the remaining human step for ordinary channel OAuth, not a simulated AI copyright scan. An explicit copyright rejection blocks release. A pass is bound to the final MP4 hash, uploaded video and destination channel; changes invalidate it.

After clearance, enabled Autopilot releases the selected visibility when its quality, daily cap and spacing rules permit. Manual release also stays inside ClipRank. Existing OAuth connections may need reconnecting to grant `youtube.force-ssl` for changing visibility. No live upload or account authorization is performed during setup.

Legacy researched explainers remain supported for existing productions; they are no longer the default strategy or discovery engine.

### Pocket TTS (default local voice)

Pocket TTS is integrated into ClipRank's existing backend. No separate speech server is required. Select Alba, Marius or Javert in Settings and Ranking, or **Local Pocket TTS** in Autopilot. It runs on the Mac's CPU and does not send narration to a speech service.

Install the backend requirements, then explicitly download the pinned English model, tokenizer and three voice embeddings with `PYTHONPATH=backend .venv/bin/python backend/setup_pocket_tts.py`. Files live in ignored `storage/models/pocket-tts/`. This command performs downloads; ordinary generation and previews accept only installed local assets. The existing cached Whisper base model provides measured caption timings. Inaccurate captions block export; there is no silent online or system-voice fallback.

Curious and Fast Explainer use Alba, Energetic uses Marius, and Documentary uses Javert. Each project retains voice/model provenance; YouTube uploads add attribution automatically. Alba is CC BY 4.0; Marius and Javert are CC0 donations. Non-commercial Expresso/EARS voices are excluded. See the [official voice licenses](https://huggingface.co/kyutai/tts-voices/blob/main/README.md).

The worker caches the model and voice states, serializes CPU synthesis, and copies the voice state for each new narration. Speech is normalized to 48 kHz mono before mixing into the final AAC export. A caption rejection retries neural synthesis once with the same checks; a second rejection feeds the failed sentence back into the bounded script-repair workflow.

### Other narration options

Kokoro remains an optional engine. Install its runtime with `.venv/bin/python -m pip install -r backend/requirements-local-voice.txt`. Connect existing `kokoro-v1.0.onnx` and `voices-v1.0.bin` in `storage/models/kokoro/`, or set `CLIPRANK_KOKORO_MODEL` and `CLIPRANK_KOKORO_VOICES`. An installed cached faster-whisper base model is required for actual word alignment. No model is downloaded by this adapter. Missing local voice blocks production; select online neural voice explicitly to use the existing service.

Four styles: Curious, Energetic, Documentary and Fast Explainer. Historical style names remain compatible. Speech is never accelerated to fit the timeline. Captions use measured word timestamps and fail when alignment does not match the narration.

### Autopilot

Configure the channel, narration, timezone and publication hours. Connect YouTube once in Settings. Enable Autopilot; separately select automatic publishing if desired. Normally it researches three distinct-pillar candidates and publishes up to two after every gate passes. The optional third must exceed the exceptional threshold. Zero publications is a valid outcome.

The worker records a heartbeat every 30 seconds. Production checkpoints retain research, accepted scripts, narration and encoded segments. Expired leases recover after a restart, with a maximum of three recovery/repair attempts. Missing facts, rights, voice or quality never result in a placeholder upload. An offline computer cannot render; pending work waits for the computer.

Publishing waits for the day's candidate set to finish, compares approved quality scores, enforces a daily upload cap and at least six hours between uploads. Old unposted daily plans are not dumped onto the channel after downtime. Failed uploads retry the resumable session with a cooldown. A user pause stops new daily claims and publication; the current render can finish safely.

Temporary data is contained within `storage/temp/<server-generated-job-id>/`. After QC succeeds, the final MP4 is copied atomically to output storage and verified there, then only that job workspace is cleaned. Final MP4s, previews, scripts, provenance and QC metadata remain; upload retries use the retained final MP4. Interrupted production retains its workspace for recovery. Automatic final-file deletion is intentionally not enabled; the retention setting is stored for a later archival workflow. No LLM-supplied path is used for deletion.

## Credentials and YouTube

Install current backend requirements. New AI keys use OS credential storage (`keyring`); the SQLite row contains a reference only. YouTube authorization data is encrypted using a key stored in OS credential storage. `secure-migrate` moves existing saved credentials: `PYTHONPATH=backend .venv/bin/python backend/worker_cli.py secure-migrate`. Settings responses omit saved key values. If secure storage is unavailable, writing secrets fails with an actionable error.

Google setup checklist:

1. Create a Google Cloud project and enable YouTube Data API v3 and YouTube Analytics API.
2. Configure the OAuth consent screen and add your test account during testing.
3. Create a **Desktop app** OAuth client and load its JSON into the local Settings page.
4. Connect the intended YouTube channel. Scopes are upload, channel read-only and analytics read-only. Existing connections need reconnection for analytics consent.
5. Leave uploads private while validating. API projects that require a YouTube compliance audit may have uploads restricted to private until approved. This app does not bypass that restriction.
6. Review the configured audience designation, privacy, attribution and any explicitly configured affiliate disclosure before enabling automatic publication.

API uploads run directly from the worker. The initial Google consent page is unavoidable; subsequent uploads do not redirect to YouTube Studio. Tokens and resumable-session URLs are not returned to the dashboard.

Analytics uses official Data/Analytics APIs. Missing or delayed values remain missing. Learning requires at least five observations per content pillar and uses shrinkage toward the channel mean; one successful video cannot trigger a major strategy shift. Observed per-video metrics are labeled with their reporting window. No revenue estimate is fabricated. Affiliate links are included only when a user-configured product is actually discussed, with the supplied disclosure.

## Vercel Hobby control plane — prepared, not deployed

The requested cloud target is **Vercel Hobby only**, Supabase and your Mac worker. Vercel Hobby is restricted to personal non-commercial use; using it for a monetized business is not a supported use under Vercel's published policy. Free allowances and policies can change. This repository does not promise free commercial hosting.

The `cloud/` Next.js app contains only authentication, profile configuration, a daily scheduler, a durable database queue and worker heartbeat/status. It never downloads videos, runs inference, renders or uploads videos. The worker makes outbound HTTPS requests; no public port is opened on the Mac.

1. Create your Supabase project and apply `cloud/supabase/migrations/001_control_plane.sql`.
2. Run `cloud/supabase/tests/security.sql` in a disposable pgTAP-enabled test database. Validate RLS and privileges before public deployment.
3. Create your owner account via Supabase Auth; dashboard sign-in intentionally does not create arbitrary public accounts. Configure the email template to include `{{ .Token }}` for OTP sign-in.
4. Set the environment variables from `cloud/.env.example`. The service-role key and cron secret must be server-only. Never prefix them with `NEXT_PUBLIC_`.
5. Deploy the **cloud directory** as the Vercel project root. The included single daily cron is `02:00 UTC`; Hobby can invoke it anywhere in that hour. Publication times remain local worker decisions.
6. Sign in, save your channel profile and create a pairing token. Pair on the Mac with `PYTHONPATH=backend .venv/bin/python backend/worker_cli.py pair`. The token is entered through a hidden prompt and stored in OS secret storage. Pairing revokes previous worker tokens atomically.
7. Keep YouTube and AI credentials on the Mac. The cloud receives lightweight status, configured channel metadata and published video IDs only.

SQL functions claim with `FOR UPDATE SKIP LOCKED`; user tables use RLS. Worker API actions validate typed input and do not accept commands, download paths or render paths. Polling failures pause new autonomous publication until the paired controller is reachable.

**Cloud acceptance remains pending** until your Supabase project is configured, SQL/RLS tests pass against it, and a deployed dashboard is paired with the Mac. A local build cannot prove a live cloud integration.

## macOS worker launcher

Run `.venv/bin/python worker/package_macos.py` to create `storage/worker-package/ClipRank Worker.app` and a prepared login LaunchAgent plist. This is a local launcher tied to this checkout and Python environment, not a signed standalone distribution. It does not bundle models or Python.

Opening the app shows worker status and pause/resume controls. Add it to macOS Login Items manually if desired, or review and install the generated LaunchAgent yourself. Do not install both launch methods. This request prepares the files; it does not silently enable a login service.

## Verification scope

Tests cover leases/recovery, daily-plan idempotency, provenance failures, exact source evidence, API key redaction, foreign-origin rejection, bounded cloud inference, publishing caps and genuine FFmpeg graphics exports. Test fixtures use explicitly marked synthetic audio; production never substitutes that audio. YouTube publication and cloud deployment require connected accounts and remain untested live until configured.
