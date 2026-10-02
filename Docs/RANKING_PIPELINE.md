# Ranking Video Pipeline Documentation

## Overview
Workflow B takes a single user intent (e.g. *"Top 5 Parkour Fails"* or *"Top 7 Football Saves"*) and autonomously discovers sources, extracts moments, ranks them, writes an escalating retention script, records TTS narration, and edits a vertical 9:16 countdown Short.

## Pipeline Architecture
```text
User Topic (e.g. "Top 5 Parkour Fails")
        │
        ▼
   Topic & Count Parsing
        │
   Search Query Generation
        │
   Permitted Source Discovery & Ingestion
        │
   Scene Detection & Candidate Moment Extraction
        │
   Moment Deduplication (Perceptual & Timestamp)
        │
   Multi-Factor Ranking Engine
        │
   Escalating Progression Ordering (#5 → #4 → #3 → #2 → #1)
        │
   Retention Script & Hook Generation
        │
   TTS Voice-over Generation
        │
   Vertical 9:16 Reframing & Countdown Badges
        │
   Dynamic Captions & Audio Mixing
        │
   FFmpeg Faststart Encoding
        │
   Temporary File Purge (storage/temp/<job_id>)
        │
   Finished Ranking Short (Preview & Download)
```

## Retention Countdown Structure (Section 8)
- **0:00 - 0:03 HOOK**: High-energy opening teaser teasing the #1 payoff.
- **#5**: Curiosity setup → action clip → transition bridge.
- **#4**: Escalation setup → action clip → transition bridge.
- **#3**: Tension setup → action clip → transition bridge.
- **#2**: High stakes setup → action clip → strong anticipation.
- **#1**: Climax & peak payoff → concise call-to-action outro.
