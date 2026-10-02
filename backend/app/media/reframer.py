from pathlib import Path
from app.core.config import VIDEO_WIDTH, VIDEO_HEIGHT
from app.core.runtime import run_process
from app.media.ffmpeg_core import FFmpegCore



class VideoReframer:
    @staticmethod
    def reframe_to_vertical(input_video: Path, output_video: Path, start: float = 0,
                            duration: float = 30, overlay_png: Path = None,
                            width: int = VIDEO_WIDTH, height: int = VIDEO_HEIGHT,
                            layout: str = "fill", captions_ass: Path = None,
                            narration_wav: Path = None, title_band: bool = False) -> Path:
        info = FFmpegCore.get_video_info(input_video)
        if not info["has_video"] or start < 0 or start >= info["duration"]:
            raise ValueError("Selected moment is outside the source video.")
        duration = min(duration, info["duration"] - start)
        if duration < .5:
            raise ValueError("Selected moment is too short to render.")
        output_video.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(input_video)]
        next_input = 1
        overlay_index = None
        if overlay_png:
            overlay_index = next_input
            next_input += 1
            cmd += ["-loop", "1", "-i", str(overlay_png)]
        narration_index = None
        if narration_wav:
            narration_index = next_input
            next_input += 1
            cmd += ["-i", str(narration_wav)]
        caption_index = None
        if captions_ass and captions_ass.exists():
            from app.media.captions import CaptionRenderer
            caption_track = CaptionRenderer.render_ass_track(captions_ass, duration, width, height)
            caption_index = next_input
            next_input += 1
            cmd += ["-i", str(caption_track)]
        silent_index = None
        if not info["has_audio"]:
            silent_index = next_input
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        top = 190 if title_band else 0
        bottom = 115 if title_band else 0
        canvas_h = height - top - bottom
        if layout == "fit":
            filters = [
                f"[0:v]split=2[b][f]",
                f"[b]scale={width}:{canvas_h}:force_original_aspect_ratio=increase,crop={width}:{canvas_h},boxblur=18:3[bg]",
                f"[f]scale={width}:{canvas_h}:force_original_aspect_ratio=decrease[fg]",
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[frame]",
            ]
        else:
            filters = [f"[0:v]scale={width}:{canvas_h}:force_original_aspect_ratio=increase,crop={width}:{canvas_h}[frame]"]
        filters.append(f"[frame]pad={width}:{height}:0:{top}:black,setsar=1,fps=30,setpts=PTS-STARTPTS[canvas]")
        visual = "canvas"
        if overlay_index is not None:
            overlay_enable = "" if title_band else ":enable='lt(t,3)'"
            filters.append(f"[{visual}][{overlay_index}:v]overlay=0:0:shortest=1{overlay_enable}[decorated]")
            visual = "decorated"
        if caption_index is not None:
            filters.append(f"[{visual}][{caption_index}:v]overlay=0:0:shortest=1[captioned]")
            visual = "captioned"
        audio_input = "0:a" if info["has_audio"] else f"{silent_index}:a"
        filters.append(f"[{audio_input}]aresample=44100,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,apad[original]")
        if narration_index is not None:
            filters.append("[original]volume=0.22[quiet]")
            filters.append(f"[{narration_index}:a]aresample=44100,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,apad[voice]")
            filters.append("[quiet][voice]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[outa]")
        else:
            filters.append("[original]anull[outa]")
        cmd += ["-filter_complex", ";".join(filters), "-map", f"[{visual}]", "-map", "[outa]",
                "-t", f"{duration:.3f}", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                "-movflags", "+faststart", str(output_video)]
        run_process(cmd)
        return output_video
