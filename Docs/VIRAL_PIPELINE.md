# Viral Clips Pipeline Documentation

## Overview
Workflow A automatically transforms a long-form video or video URL into multiple standalone, high-retention viral 9:16 Shorts/Reels.

## Pipeline Architecture
```text
Source Video (URL / Upload)
        │
        ▼
   Ingestion & Validation
        │
   Audio Extraction (WAV)
        │
   Speech Transcription (Whisper timestamps)
        │
   Local Scene & Audio Spike Detection
        │
   Viral Moment Scoring (0-100)
        │
   Clip Selection & Context Boundary Optimization (Setup → Action → Payoff)
        │
   Vertical 9:16 Reframing (Blur background + Fitted subject)
        │
   Dynamic Captions & Hook Overlay
        │
   Final Render (FFmpeg faststart)
        │
   Temporary File Purge (storage/temp/<job_id>)
        │
   Finished Viral Clips (Preview & Download)
```

## Viral Scoring Dimensions
1. **Hook Strength**: First 3-second retention magnet.
2. **Emotional Valence**: Laughter, awe, shock, curiosity.
3. **Information Density**: Words per second and storyline pacing.
4. **Visual Activity**: Motion vector intensity and scene transitions.
5. **Clip Independence**: Story completeness without needing the full video context.
