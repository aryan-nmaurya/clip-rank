import logging
from pathlib import Path
from app.core.config import VIRAL_OUTPUT_DIR
from app.core.database import update_job, update_project, create_or_update_clip
from app.core.runtime import run_blocking
from app.storage.manager import StorageManager
from app.sources.ingestion import SourceIngestion
from app.ai.router import AIRouter
from app.media.ffmpeg_core import FFmpegCore
from app.media.reframer import VideoReframer
from app.media.captions import CaptionRenderer, clean_label
from app.media.moments import MomentAnalyzer
from app.transcription.transcriber import Transcriber

logger = logging.getLogger("ai_shorts.viral_pipeline")


class ViralPipeline:
    @classmethod
    async def run(cls, job_id, project_id, video_source, count, settings, on_progress):
        temp = StorageManager.get_job_temp_dir(job_id)
        warnings = []

        def progress(status, value, stage):
            update_job(job_id, status=status, progress=value, current_stage=stage)
            on_progress(status, value, stage)

        try:
            progress("INGESTING", 10, "Loading actual source footage")
            if not video_source:
                raise ValueError("A video URL or uploaded file is required.")
            if str(video_source).startswith(("http://", "https://")):
                video, metadata = await run_blocking(SourceIngestion.download_video, video_source, temp / "downloads" / "source.mp4")
            else:
                video = await run_blocking(SourceIngestion.ingest_video_file, Path(video_source), temp / "downloads" / "source.mp4")
                metadata = {"title": settings.get("source_title") or Path(video_source).stem, "url": None, "source_id": project_id}
            update_project(project_id, title=f"Clips · {clean_label(metadata['title'], 12)}")
            info = await run_blocking(FFmpegCore.get_video_info, video)
            transcription = {"segments": [], "text": ""}
            if info["has_audio"]:
                progress("TRANSCRIBING", 25, "Recognizing source speech and word timestamps")
                audio = await run_blocking(FFmpegCore.extract_audio, video, temp / "audio" / "speech.wav")
                transcription = await run_blocking(Transcriber.transcribe, audio)
                if transcription.get("warning"):
                    warnings.append(transcription["warning"])
            else:
                warnings.append("Source has no audio; exported clips contain a silent audio track.")
            progress("ANALYZING", 45, "Measuring motion and clarity across source frames")
            moments = await run_blocking(MomentAnalyzer.analyze, video, count=count,
                                        target_duration=settings.get("target_duration", 25),
                                        segments=transcription["segments"], title=metadata["title"])
            sheet = await run_blocking(MomentAnalyzer.contact_sheet, video, moments, temp / "frames" / "evidence.jpg")
            moments, basis = await AIRouter.evaluate_moments(moments, sheet, settings, transcript=transcription["segments"])
            moments = MomentAnalyzer.clamp_selections(moments, info["duration"], count)
            if not moments:
                raise ValueError("No complete moments could be selected from this source.")
            if len(moments) < count:
                warnings.append(f"Source supports {len(moments)} distinct clips; {count} were requested.")
            if "visual metrics" in basis:
                warnings.append("Scores use measured visual activity and clarity. Configure an image-capable AI model for semantic analysis of hooks and payoff.")
            selected = []
            for idx, moment in enumerate(moments):
                progress("EDITING", 65 + round(idx / len(moments) * 23), f"Rendering highlight {idx + 1} of {len(moments)}")
                title = clean_label(moment.get("label") or moment.get("title") or "Highlight", 9)
                if len(moments) > 1 and not moment.get("label"):
                    title = f"{title} · {idx + 1}"
                start, end = moment["start"], moment["end"]
                overlay = await run_blocking(CaptionRenderer.render_overlay_card, temp / "renders" / f"hook_{idx}.png", title=title)
                captions = await run_blocking(CaptionRenderer.write_ass, temp / "renders" / f"captions_{idx}.ass",
                                             transcription["segments"], start, end) if settings.get("captions", True) else None
                rendered = await run_blocking(VideoReframer.reframe_to_vertical, video, temp / "renders" / f"clip_{idx}.mp4",
                                             start=start, duration=end-start, overlay_png=overlay,
                                             captions_ass=captions, layout=settings.get("layout", "fit"))
                clip_info = await run_blocking(FFmpegCore.validate_output, rendered, end-start)
                clip_id = f"{project_id}_{job_id}_viral_{idx + 1}"
                final = await run_blocking(StorageManager.move_final_clip, rendered, "viral", clip_id)
                await run_blocking(FFmpegCore.extract_frame, final, VIRAL_OUTPUT_DIR / f"{clip_id}_preview.jpg", min(2, (end-start)/2))
                create_or_update_clip(clip_id=clip_id, project_id=project_id, job_id=job_id, title=title,
                                      subtitle=f"Potential {moment['score']}/100 · {clip_info['duration']:.1f}s",
                                      duration=round(clip_info["duration"], 2), viral_score=moment["score"],
                                      reason=moment["reason"], status="READY",
                                      preview_path=f"/output/viral/{clip_id}_preview.jpg", video_path=f"/output/viral/{clip_id}.mp4")
                selected.append({**metadata, **moment, "title": title, "clip_id": clip_id})
            update_project(project_id, result_data={"sources": [metadata], "moments": selected,
                                                   "analysis_basis": basis, "warnings": warnings,
                                                   "requested_count": count, "generated_count": len(selected),
                                                   "scores_are_estimates": True})
            progress("CLEANING", 98, "Removing temporary render files")
            StorageManager.cleanup_job_temp(job_id)
            update_project(project_id, status="COMPLETED")
            progress("COMPLETED", 100, "Clips ready")
        except Exception as exc:
            logger.exception("Viral generation failed for %s", job_id)
            update_job(job_id, status="FAILED", current_stage="Failed", error_message=str(exc)[:1000], detailed_error=str(exc))
            update_project(project_id, status="FAILED")
            on_progress("FAILED", 0, "Failed")
            raise
        finally:
            StorageManager.cleanup_job_temp(job_id)
