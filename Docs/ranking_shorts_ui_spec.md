# Ranking Shorts AI — Simple One-Click UI & Product Specification

## 1. Product Goal

Build an **autonomous ranking-video generator** with an extremely simple user experience. The AI layer must support both **local models** and **Google AI Studio (Gemini API)**, selectable in Settings.

The user should only need to provide a topic such as:

> **Top 5 Parkour Fails**

Then press **Generate**.

The system handles everything else automatically:

- finds permitted source videos
- downloads / ingests them
- detects candidate moments
- understands what happens in each moment
- scores and ranks the moments
- chooses the best clips
- writes an engaging countdown script
- generates voice-over
- creates captions
- edits the final vertical video
- creates **two different Shorts**
- performs quality checks
- shows preview cards
- provides a **Download** button for each finished Short

The UI should feel similar in simplicity to SupoClip: **few screens, minimal controls, obvious progress, and one-click generation**.

---

# 2. Core UX Principle

## User responsibility

Only:

```text
1. Enter topic
2. Click Generate
3. Wait
4. Preview
5. Download
```

## System responsibility

Everything else.

```text
TOPIC
  ↓
SOURCE DISCOVERY
  ↓
VIDEO INGESTION
  ↓
MOMENT DETECTION
  ↓
VISION UNDERSTANDING
  ↓
RANKING
  ↓
TOP 5 SELECTION
  ↓
SCRIPT
  ↓
VOICE
  ↓
CAPTIONS
  ↓
EDITING
  ↓
QUALITY CHECK
  ↓
TEMPORARY MEDIA CLEANUP
  ↓
2 FINISHED SHORTS
```

No prompt engineering should be required from the user.

---

# 3. Main App Layout

Desktop-first layout inspired by the simplicity of SupoClip.

```text
┌──────────────────────────────────────────────────────────────┐
│ Sidebar                │ Main Content                        │
│                        │                                     │
│ Logo                   │                                     │
│                        │                                     │
│ + New Generation       │                                     │
│                        │                                     │
│ Create                 │                                     │
│ Library                │                                     │
│ Settings               │                                     │
│                        │                                     │
│                        │                                     │
│                        │                                     │
│                        │                                     │
│ Account                │                                     │
└──────────────────────────────────────────────────────────────┘
```

Keep the sidebar narrow and persistent.

---

# 4. Sidebar

## Items

```text
Logo / Product Name

+ New Generation

Create
Library

Settings

-----------------

AI Status
Account / Profile
```

### New Generation

Primary black button.

Clicking it always opens the **Create** screen.

### Create

Main generation interface.

### Library

Shows:

- currently processing generations
- completed generations
- failed generations
- downloaded / saved generations

### Settings

Only advanced configuration lives here.

Do **not** show technical AI settings during normal generation.

---

# 5. Create Screen

This is the most important screen.

It should be extremely minimal.

## Layout

```text
Create ranking videos

Turn one topic into two finished Shorts.

┌──────────────────────────────────────────────────────┐
│ What should we rank?                                 │
│                                                      │
│ Top 5 Parkour Fails                                  │
│                                                      │
└──────────────────────────────────────────────────────┘

Examples:
Top 5 Funniest Goalkeeper Fails
Top 5 Craziest Basketball Dunks
Top 5 Most Satisfying Machines
Top 5 Funny Cat Jumps

                         [ Generate Shorts ]
```

## Input

One large text field.

Placeholder:

```text
Enter a topic, e.g. "Top 5 Parkour Fails"
```

## Primary action

```text
Generate Shorts
```

Only one important action.

Do not ask the user:

- which LLM to use
- which vision model to use
- which TTS model to use
- which search provider to use
- which resolution to use
- how many source videos to scan
- ranking weights
- subtitle settings

The system decides automatically.

---

# 6. Optional Advanced Controls

Advanced controls should be hidden behind:

```text
Advanced
```

Default state: collapsed.

Possible options:

```text
Number of outputs: 2
Target duration: Auto
Language: Auto / English
Voice: Auto
Style: Auto
Source policy: Permitted sources only
```

These are optional.

The normal user should never need to open this section.

---

# 7. Generation Starts Immediately

After clicking **Generate Shorts**, immediately create a generation job.

Route:

```text
/generation/{job_id}
```

The UI switches to a processing screen.

---

# 8. Processing Screen

Use a clean progress design similar to SupoClip.

## Header

Example:

```text
Top 5 Parkour Fails

● Processing
Started 2 minutes ago
2 Shorts requested
```

Top-right actions:

```text
Cancel
Delete
```

---

# 9. Progress Card

Large progress card near the top.

Example:

```text
Creating your Shorts...

Finding the best moments across multiple videos.
```

Progress bar at top.

## Processing stages

Display simple human-readable stages:

```text
✓ Finding sources
✓ Analyzing videos
✓ Ranking moments
✓ Writing narration
● Creating Shorts
○ Final quality check
```

Do not expose technical terms such as:

```text
Qwen3-VL inference
FFmpeg concat
Whisper timestamp alignment
Gemini fallback
```

Those belong in logs, not normal UX.

---

# 10. Processing Status Mapping

Backend states:

```text
CREATED
PLANNING
DISCOVERING
DOWNLOADING
PREPROCESSING
UNDERSTANDING
RANKING
SCRIPTING
VOICE
EDITING
QUALITY_CHECK
CLEANUP
COMPLETED
FAILED
CANCELLED
```

Frontend simplified states:

| Backend state | User sees |
|---|---|
| CREATED / PLANNING | Preparing |
| DISCOVERING / DOWNLOADING | Finding sources |
| PREPROCESSING / UNDERSTANDING | Analyzing videos |
| RANKING | Ranking moments |
| SCRIPTING / VOICE | Creating narration |
| EDITING | Creating Shorts |
| QUALITY_CHECK | Final quality check |
| CLEANUP | Finishing up |
| COMPLETED | Ready |
| FAILED | Needs attention |

---

# 11. Live Result Cards

As soon as one Short is ready, show it.

Do **not** wait for both outputs.

Example:

```text
Creating Short 2/2...
1 Short ready below — another is on the way.
```

Then show:

```text
┌─────────────────────────┐
│                         │
│      VIDEO PREVIEW      │
│        9:16             │
│                         │
│          ▶              │
│                         │
│                         │
└─────────────────────────┘

Top 5 Parkour Fails — Version A

0:48

[ Download ]   [ Regenerate ]
```

When the second video becomes ready, place it beside or below the first.

---

# 12. Final Completed Screen

When everything is done:

```text
✓ Your Shorts are ready
```

Show two cards.

## Short A

```text
┌──────────────────────┐
│                      │
│      PREVIEW         │
│        ▶             │
│                      │
└──────────────────────┘

Top 5 Parkour Fails
Version A · Fast-paced

0:47

[ Download ]
```

## Short B

```text
┌──────────────────────┐
│                      │
│      PREVIEW         │
│        ▶             │
│                      │
└──────────────────────┘

Top 5 Parkour Fails
Version B · Story-driven

0:54

[ Download ]
```

Primary action on every card:

```text
Download
```

Secondary actions:

```text
Regenerate
Delete
```

Optional later:

```text
Post
Schedule
Export captions
```

Do not add these in MVP.

---

# 13. Video Preview

Clicking the thumbnail opens a modal player.

```text
┌───────────────────────────────────────────┐
│                                       ×   │
│                                           │
│                VIDEO                      │
│                                           │
│                  ▶                        │
│                                           │
│                                           │
├───────────────────────────────────────────┤
│ Top 5 Parkour Fails — Version A           │
│                                           │
│              [ Download ]                 │
└───────────────────────────────────────────┘
```

Keep controls minimal.

---

# 14. Library Screen

The Library should look like a simple generation history.

## Sections

```text
Library

[ Search generations... ]

All    Processing    Completed    Failed
```

Cards:

```text
┌─────────────────────────────────────────────────────┐
│ Thumbnail   Top 5 Parkour Fails                     │
│             Completed · 2 Shorts                    │
│             12 minutes ago                          │
│                                                     │
│             [ Open ]                                │
└─────────────────────────────────────────────────────┘
```

Another:

```text
┌─────────────────────────────────────────────────────┐
│ Thumbnail   Top 5 Goalkeeper Fails                  │
│             Processing · Ranking moments            │
│             3 minutes ago                           │
│                                                     │
│             [ Open ]                                │
└─────────────────────────────────────────────────────┘
```

---

# 15. Settings Screen

Keep Settings separate from generation.

## General

```text
Output language
Default voice
Output folder
Number of Shorts
```

## AI

The application must support **both local AI and Google AI Studio (Gemini API)**. The user chooses the preferred mode once in Settings; normal generation remains one-click.

Default:

```text
AI Mode: Automatic
```

Available modes:

```text
Automatic
Local AI only
Google AI Studio only
```

### Automatic mode

```text
local model first
↓
confidence check
↓
Google AI Studio / Gemini fallback when necessary
```

### Local AI only

All supported reasoning and vision tasks stay on-device. If a task cannot be completed confidently, the system must fail gracefully rather than silently upload media to a cloud model.

### Google AI Studio only

Use the configured Google AI Studio API key for supported text and vision reasoning tasks. The key is configured once in Settings and is never requested during normal generation.

Optional expert settings:

```text
AI Mode
Text model
Vision model
Google AI Studio API key
Gemini model
Local model endpoint
Maximum cloud requests
```

---

# 16. AI Status

Bottom-left sidebar can show the currently active AI mode:

```text
● AI Ready · Automatic
```

Possible statuses:

```text
● AI Ready · Automatic
● AI Ready · Local
● AI Ready · Google AI Studio
● Processing
● Downloading local model
● Using Google AI Studio
● Offline
```

Clicking status opens technical diagnostics.

Normal users should not see diagnostics unless they ask.

---

# 17. Generation Detail Page

Each generation stores:

```text
Topic
Status
Created time
Source count
Candidate count
Final selected moments
Short A
Short B
```

Optional expandable section:

```text
Generation details
```

Inside:

```text
Sources analyzed: 42
Candidate moments: 117
Finalists: 14
Moments used: 10
```

Do not show this by default.

---

# 18. Error UX

Never show raw stack traces.

Bad:

```text
FFmpeg exited with code 1
```

Good:

```text
We couldn't finish rendering this Short.

[ Retry ]
```

Technical details may be hidden under:

```text
View details
```

---

# 19. Retry Logic

The system must retry only the failed stage.

Example:

```text
Finding sources       ✓
Analyzing videos      ✓
Ranking moments       ✓
Creating narration    ✓
Creating Shorts       FAILED
```

Clicking Retry should restart:

```text
EDITING
```

not:

```text
SOURCE DISCOVERY
```

This saves a lot of time.

---

# 20. Temporary Media Lifecycle & Automatic Cleanup

All downloaded source media must be treated as **temporary job data**. The system must automatically delete downloaded videos and intermediate media after the final Shorts are rendered and verified.

## Job workspace

Every generation gets its own controlled workspace:

```text
jobs/
└── job_01HXYZ/
    ├── temp/
    │   ├── sources/
    │   ├── audio/
    │   ├── frames/
    │   ├── candidates/
    │   ├── voice/
    │   └── render_cache/
    │
    └── outputs/
        ├── short_a.mp4
        └── short_b.mp4
```

## Delete automatically

After successful rendering, delete:

```text
downloaded source videos
downloaded source audio
extracted audio tracks
sampled frames
scene thumbnails
candidate clip files
temporary cropped clips
temporary subtitle files
temporary narration chunks
render fragments
FFmpeg cache files
vision-analysis frame batches
```

## Keep only

```text
short_a.mp4
short_b.mp4
preview thumbnails
generation metadata
ranking metadata
source URLs / source IDs
timestamps
scores
script text
job logs
```

The application must **not retain copies of the original downloaded videos** once the final outputs have been verified.

## Successful-job cleanup order

```text
Render Short A
↓
Render Short B
↓
Verify both MP4 files
↓
Run quality checks
↓
Move final MP4s to outputs/
↓
Delete temp/
↓
Mark job COMPLETED
```

Never delete the source media before both final outputs are verified.

## Failed jobs

For an unrecoverable failure:

```text
Save lightweight diagnostics
↓
Delete downloaded source videos
↓
Delete intermediate media
↓
Keep only logs / metadata
```

## Cancelled jobs

If the user clicks Cancel:

```text
Stop active workers
↓
Wait for FFmpeg / model processes to release files
↓
Delete the job temp directory
↓
Mark CANCELLED
```

## Crash recovery

On application startup, scan unfinished job workspaces. Resume a recoverable job or clean abandoned temporary media. Downloaded videos must never accumulate indefinitely after crashes or restarts.

## Deletion safety

Cleanup code may delete only paths inside the application's controlled temporary directory:

```text
<app-data>/jobs/<job-id>/temp/
```

An LLM must never be allowed to provide an arbitrary filesystem path to the deletion routine.

---

# 21. Backend Job Object

Example:

```json
{
  "id": "job_01HXYZ",
  "topic": "Top 5 Parkour Fails",
  "status": "EDITING",
  "progress": 82,
  "outputs_requested": 2,
  "outputs_ready": 1,
  "created_at": "2026-10-02T12:00:00Z"
}
```

---

# 22. Short Output Object

```json
{
  "id": "short_a",
  "job_id": "job_01HXYZ",
  "variant": "A",
  "style": "fast_paced",
  "duration": 47.2,
  "status": "READY",
  "preview_path": "/outputs/job_01HXYZ/a_preview.jpg",
  "video_path": "/outputs/job_01HXYZ/short_a.mp4"
}
```

---

# 23. Recommended Frontend

For a desktop/web interface:

```text
Next.js
React
Tailwind CSS
shadcn/ui
Lucide icons
```

Alternative ultra-simple local app:

```text
React + Vite
```

Backend:

```text
Python
FastAPI
SQLite
```

Communication:

```text
REST API
+
Server-Sent Events (SSE)
```

SSE is enough for live generation progress.

WebSockets are not necessary for MVP.

---

# 24. Recommended Routes

Frontend:

```text
/
    Redirect to /create

/create
    New generation

/generation/:id
    Processing + results

/library
    Generation history

/settings
    Configuration
```

Backend:

```text
POST   /api/generations
GET    /api/generations/:id
GET    /api/generations
POST   /api/generations/:id/cancel
POST   /api/generations/:id/retry
DELETE /api/generations/:id

GET    /api/generations/:id/events

GET    /api/shorts/:id
GET    /api/shorts/:id/download
POST   /api/shorts/:id/regenerate
```

---

# 25. Create Generation API

Request:

```json
{
  "topic": "Top 5 Parkour Fails"
}
```

Response:

```json
{
  "job_id": "job_01HXYZ",
  "status": "CREATED"
}
```

Frontend immediately navigates to:

```text
/generation/job_01HXYZ
```

---

# 26. Progress Events

Example SSE messages:

```json
{
  "stage": "DISCOVERING",
  "progress": 14,
  "message": "Finding source videos"
}
```

```json
{
  "stage": "RANKING",
  "progress": 58,
  "message": "Ranking the best moments"
}
```

```json
{
  "stage": "EDITING",
  "progress": 84,
  "message": "Creating Short 1 of 2"
}
```

```json
{
  "stage": "EDITING",
  "progress": 89,
  "outputs_ready": 1,
  "message": "1 Short ready — another is on the way"
}
```

---

# 27. One-Click Orchestration

The frontend should make only one important call:

```text
POST /api/generations
```

Everything else is controlled by the backend orchestrator.

```text
create_job(topic)

    ↓

plan_topic()

    ↓

discover_sources()

    ↓

ingest_sources()

    ↓

detect_candidate_moments()

    ↓

analyze_candidates()

    ↓

remove_duplicates()

    ↓

rank_candidates()

    ↓

choose_two_sets()

    ↓

write_scripts()

    ↓

generate_voice()

    ↓

render_short_A()
render_short_B()

    ↓

quality_check()

    ↓

cleanup_temporary_media()

    ↓

COMPLETED
```

---

# 28. Two Output Strategies

The two outputs should not simply be duplicate renders.

## Version A

```text
Fast-paced
Strong hook
Short commentary
Rapid cuts
High energy
~40–50 seconds
```

## Version B

```text
Story-driven
More suspense
More commentary
Slower escalation
~50–60 seconds
```

The UI only labels them:

```text
Version A
Version B
```

No configuration required.

---

# 29. Retention Script Format

Each generated Short should follow:

```text
HOOK

#5 setup
clip

bridge

#4 setup
clip

bridge

#3 setup
clip

bridge

#2 setup
clip

strong anticipation

#1 setup
clip

short ending
```

Example:

```text
"These parkour fails get worse every single time,
and number one looked impossible to recover from."

#5
"We're starting with a jump that looked completely
fine... until the landing."

[CLIP]

"And number four somehow makes an even simpler
jump go completely wrong."

[CLIP]

"Number three is where things start getting painful."

[CLIP]

"But watch number two closely — the mistake happens
in less than a second."

[CLIP]

"And none of those compare to number one."

[CLIP]
```

---

# 30. UX Rules

The app must follow these rules:

1. **One primary action per screen.**
2. Never ask the user technical AI questions during generation.
3. Always show clear progress.
4. Show a finished Short immediately when it becomes ready.
5. Keep download visible.
6. Never expose internal errors by default.
7. Preserve jobs so users can close and reopen the app.
8. Resume interrupted jobs when possible.
9. Do not require user supervision during generation.
10. If the system needs cloud fallback, handle it automatically when configured.
11. Make the normal workflow possible without opening Settings.
12. Do not make the user manually select candidate clips.
13. Do not require prompt editing.
14. Do not make users manually write narration.
15. Do not make users manually edit captions.
16. Treat every downloaded source video as temporary media.
17. After both final Shorts pass quality checks, delete downloaded source videos automatically.
18. Delete extracted audio, sampled frames, temporary clips, render fragments, and other intermediate media automatically.
19. Run cleanup after cancellation or unrecoverable failure whenever temporary media exists.
20. Never delete the final Shorts during automatic cleanup.
21. Never send media to Google AI Studio when the user selected Local AI only.

---

# 31. Visual Style

Use:

```text
Background: near-white
Panels: white
Text: near-black
Borders: light gray
Accent: one strong accent color
Rounded cards
Large whitespace
Minimal shadows
```

Typography:

```text
Inter
or
Geist
```

Design philosophy:

```text
clean
premium
quiet
minimal
obvious
```

Avoid:

```text
complex dashboards
charts
dozens of toggles
developer terminology
large configuration forms
```

---

# 32. Create Screen Wireframe

```text
┌───────────────────────────────────────────────────────────────┐
│ Logo           │                                             │
│                │   Create ranking videos                     │
│ + New          │                                             │
│ Generation     │   Turn one topic into two finished Shorts.  │
│                │                                             │
│ Create         │   ┌───────────────────────────────────────┐ │
│ Library        │   │ What should we rank?                  │ │
│                │   │                                       │ │
│ Settings       │   │ Top 5 Parkour Fails                   │ │
│                │   │                                       │ │
│                │   └───────────────────────────────────────┘ │
│                │                                             │
│                │                         [ Generate Shorts ]  │
│                │                                             │
│                │   Try:                                      │
│                │   Top 5 Funniest Goalkeeper Fails           │
│                │   Top 5 Crazy Basketball Dunks              │
│                │                                             │
│ ● AI Ready     │                                             │
└───────────────────────────────────────────────────────────────┘
```

---

# 33. Processing Screen Wireframe

```text
┌───────────────────────────────────────────────────────────────┐
│ Logo           │ Top 5 Parkour Fails               Cancel    │
│                │ ● Processing                                 │
│ + New          │                                             │
│ Generation     │ ┌─────────────────────────────────────────┐ │
│                │ │ Creating your Shorts...                 │ │
│ Create         │ │ ███████████████████████░░░░  78%        │ │
│ Library        │ │                                         │ │
│                │ │ ✓ Finding sources                       │ │
│ Settings       │ │ ✓ Analyzing videos                      │ │
│                │ │ ✓ Ranking moments                       │ │
│                │ │ ✓ Writing narration                     │ │
│                │ │ ● Creating Shorts                       │ │
│                │ │ ○ Final quality check                   │ │
│                │ └─────────────────────────────────────────┘ │
│                │                                             │
│                │ ┌──────────────┐                            │
│                │ │ VIDEO        │                            │
│                │ │ PREVIEW      │                            │
│                │ │      ▶       │                            │
│                │ └──────────────┘                            │
│                │                                             │
│                │ Version A · 0:47                            │
│                │ [ Download ]                                │
│                │                                             │
│ ● Processing   │ Short 2 is still being created...          │
└───────────────────────────────────────────────────────────────┘
```

---

# 34. Completed Screen Wireframe

```text
┌───────────────────────────────────────────────────────────────┐
│ Logo           │ Top 5 Parkour Fails                          │
│                │ ✓ Ready                                      │
│ + New          │                                              │
│ Generation     │ ┌──────────────┐   ┌──────────────┐          │
│                │ │              │   │              │          │
│ Create         │ │   SHORT A    │   │   SHORT B    │          │
│ Library        │ │      ▶       │   │      ▶       │          │
│                │ │              │   │              │          │
│ Settings       │ └──────────────┘   └──────────────┘          │
│                │                                              │
│                │ Fast-paced           Story-driven            │
│                │ 0:47                 0:54                    │
│                │                                              │
│                │ [ Download ]          [ Download ]            │
│                │                                              │
│ ● AI Ready     │                                              │
└───────────────────────────────────────────────────────────────┘
```

---

# 35. MVP Scope

## Must have

```text
Topic input
Generate button
Processing page
Progress stages
2 generated Shorts
Preview
Download
Library
Retry
Delete
AI Mode selection in Settings
Google AI Studio API key support
Local AI support
Automatic temporary-media cleanup
```

## Do not build yet

```text
Analytics
Social posting
Scheduling
Teams
Billing
Comments
Collaboration
Timeline editor
Manual subtitle editor
Complex voice marketplace
Template marketplace
Mobile app
```

---

# 36. MVP Definition of Done

The MVP is finished when a user can:

```text
1. Open application.

2. Type:
   "Top 5 Parkour Fails"

3. Click:
   Generate Shorts

4. Leave the computer alone.

5. Return later.

6. See:
   Short A
   Short B

7. Preview either video.

8. Click:
   Download

9. Receive valid MP4 files.
```

Nothing else is required for Version 0.1.

---

# 37. Product Philosophy

The product should feel like:

```text
Topic → Generate → Download
```

Not:

```text
Topic
→ choose model
→ configure search
→ configure voice
→ choose captions
→ choose ranking method
→ select sources
→ edit timeline
→ export
```

All technical complexity belongs inside the system.

The best version of this product is one where the user does not care whether the system used:

```text
local LLM
Qwen vision
Google AI Studio / Gemini
Whisper
Kokoro
FFmpeg
OpenCV
```

They only care that:

```text
I gave it a topic.

It created two good Shorts.

I downloaded them.

Temporary source media was cleaned up automatically.
```

The user can choose **Automatic**, **Local AI only**, or **Google AI Studio only** in Settings. That infrastructure choice must not add friction to the normal Topic → Generate → Download workflow.
