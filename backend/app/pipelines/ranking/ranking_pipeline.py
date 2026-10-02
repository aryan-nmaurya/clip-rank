import logging
from app.core.config import RANKING_OUTPUT_DIR
from app.core.database import update_job, update_project, create_or_update_clip
from app.core.runtime import run_blocking
from app.storage.manager import StorageManager
from app.ai.router import AIRouter
from app.sources.discovery import SourceDiscovery
from app.pipelines.ranking.topic_parser import TopicParser
from app.pipelines.ranking.scorer import Deduplicator, RankingScorer
from app.tts.voice_engine import TTSEngine
from app.media.reframer import VideoReframer
from app.media.captions import CaptionRenderer, clean_label
from app.media.ffmpeg_core import FFmpegCore
from app.media.moments import MomentAnalyzer
from pathlib import Path

logger = logging.getLogger("ai_shorts.ranking_pipeline")


class RankingPipeline:
    @classmethod
    async def run(cls, job_id, project_id, topic, count, settings, on_progress):
        temp = StorageManager.get_job_temp_dir(job_id)

        def progress(status, value, stage):
            update_job(job_id, status=status, progress=value, current_stage=stage)
            on_progress(status, value, stage)

        try:
            topic, inferred = TopicParser.parse_topic(topic, count or 5)
            count = count or inferred
            progress("INGESTING", 10, "Finding and downloading real candidate videos")
            sources = await run_blocking(SourceDiscovery.discover_candidate_videos, topic, count, temp,
                                        source_urls=settings.get("source_urls"), source_files=settings.get("source_files"), source_titles=settings.get("source_titles"))
            moments = []
            for idx, source in enumerate(sources):
                progress("ANALYZING", 30 + round(idx / len(sources) * 22), f"Analyzing source {idx + 1} of {len(sources)}")
                candidates = await run_blocking(MomentAnalyzer.analyze, Path(source["file_path"]), count=3,
                                               target_duration=settings.get("segment_duration", 7), title=source["title"])
                sheet = await run_blocking(MomentAnalyzer.contact_sheet, Path(source["file_path"]), candidates,
                                          temp / "frames" / f"source_{idx}.jpg")
                revised, basis = await AIRouter.evaluate_moments(candidates, sheet, settings, topic=topic)
                chosen = MomentAnalyzer.clamp_selections(revised, source["duration"], 1)
                if chosen and chosen[0].get("topic_relevance", 1) >= .45 and not chosen[0].get("already_ranked", False):
                    moments.append({**source, **chosen[0], "label": clean_label(chosen[0].get("label") or source["title"], 4)})
            progress("RANKING", 56, "Ordering distinct moments from #N to the best at #1")
            ordered = RankingScorer.score_and_order(Deduplicator.deduplicate_moments(moments), count)
            rendered_clips = []
            expected_duration = 0
            for idx, moment in enumerate(ordered):
                rank = moment["assigned_rank"]
                progress("EDITING", 65 + round(idx / len(ordered) * 22), f"Editing #{rank}: {moment['label']}")
                overlay = await run_blocking(CaptionRenderer.render_overlay_card, temp / "renders" / f"rank_{rank}.png",
                                            title=topic, caption_text=moment.get("creator", ""), rank=rank, ranking_items=ordered)
                voice = None
                if settings.get("narration", False):
                    voice = temp / "voice" / f"rank_{rank}.wav"
                    text = f"Number {rank}. {moment['label']}."
                    voice_duration = await run_blocking(TTSEngine.synthesize, text, voice, settings.get("default_voice", "Samantha"))
                    if voice_duration > moment["end"] - moment["start"]:
                        raise ValueError("Narration exceeds the selected footage. Increase seconds per clip or turn narration off.")
                duration = moment["end"] - moment["start"]
                clip = await run_blocking(VideoReframer.reframe_to_vertical, Path(moment["file_path"]),
                                         temp / "renders" / f"segment_{rank}.mp4", start=moment["start"], duration=duration,
                                         overlay_png=overlay, narration_wav=voice, title_band=True, layout=settings.get("layout", "fill"))
                rendered_clips.append(clip)
                expected_duration += duration
            progress("RENDERING", 90, "Rendering and checking the countdown export")
            final_render = await run_blocking(FFmpegCore.concatenate_clips, rendered_clips, temp / "renders" / "ranking.mp4")
            info = await run_blocking(FFmpegCore.validate_output, final_render, expected_duration)
            clip_id = f"{project_id}_{job_id}_ranking"
            final = await run_blocking(StorageManager.move_final_clip, final_render, "ranking", clip_id)
            await run_blocking(FFmpegCore.extract_frame, final, RANKING_OUTPUT_DIR / f"{clip_id}_preview.jpg", 1)
            create_or_update_clip(clip_id=clip_id, project_id=project_id, job_id=job_id, title=f"Ranking {topic}",
                                  subtitle=f"Top {count} · {info['duration']:.1f}s · Source audio" if not settings.get("narration") else f"Top {count} · Narrated",
                                  duration=round(info["duration"], 2), status="READY",
                                  preview_path=f"/output/ranking/{clip_id}_preview.jpg", video_path=f"/output/ranking/{clip_id}.mp4")
            warnings = []
            if any("visual metrics" in m["analysis_basis"] for m in ordered):
                warnings.append("Ranking uses measured motion and clarity where AI analysis is unavailable. Add an image-capable AI model for semantic topic and payoff scoring.")
            # Source paths are temporary; retain URLs, credits and selected timestamps instead.
            provenance = [{k: v for k, v in m.items() if k != "file_path"} for m in ordered]
            update_project(project_id, title=f"Ranking {topic}", result_data={"moments": provenance,
                           "sources": [{k: v for k, v in s.items() if k != "file_path"} for s in sources],
                           "warnings": warnings, "scores_are_estimates": True, "style": "reference_countdown"})
            progress("CLEANING", 98, "Removing downloaded sources and render intermediates")
            StorageManager.cleanup_job_temp(job_id)
            update_project(project_id, status="COMPLETED")
            progress("COMPLETED", 100, "Countdown ready")
        except Exception as exc:
            logger.exception("Ranking generation failed for %s", job_id)
            update_job(job_id, status="FAILED", current_stage="Failed", error_message=str(exc)[:1000], detailed_error=str(exc))
            update_project(project_id, status="FAILED")
            on_progress("FAILED", 0, "Failed")
            raise
        finally:
            StorageManager.cleanup_job_temp(job_id)
