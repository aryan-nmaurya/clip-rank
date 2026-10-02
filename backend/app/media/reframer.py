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
                            narration_wav: Path = None, title_band: bool = False,
                            reveal_sfx: Path = None) -> Path:
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
        sfx_index = None
        if reveal_sfx:
            sfx_index = next_input
            next_input += 1
            cmd += ['-i', str(reveal_sfx)]
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
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        top = round(height * .12) if title_band else 0
        bottom = round(height * .12) if title_band else 0
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
        filters.append(f"[{audio_input}]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,afade=t=in:d=0.025,afade=t=out:st={max(0,duration-.04):.3f}:d=0.04,apad[original]")
        if narration_index is not None:
            voice_duration = FFmpegCore.get_video_info(narration_wav)["duration"]
            # Smooth restoration over 300 ms. Natural source impacts/reactions remain audible.
            filters.append(f"[original]volume='if(lt(t,{voice_duration:.3f}),0.18,if(lt(t,{voice_duration+.3:.3f}),0.18+0.82*(t-{voice_duration:.3f})/0.3,1))':eval=frame[quiet]")
            filters.append(f"[{narration_index}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,afade=t=out:st={max(0,voice_duration-.025):.3f}:d=0.025,apad[voice]")
            filters.append("[quiet][voice]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            filters.append("[original]anull[mix]")
        if sfx_index is not None:
            filters.append(f'[{sfx_index}:a]aresample=48000,aformat=channel_layouts=stereo,volume=.18,apad[sfx]')
            filters.append('[mix][sfx]amix=inputs=2:duration=first:normalize=0[effects]')
            mixed = 'effects'
        else:
            mixed = 'mix'
        filters.append(f'[{mixed}]alimiter=limit=0.89:level=false:latency=true[outa]')
        cmd += ["-filter_complex", ";".join(filters), "-map", f"[{visual}]", "-map", "[outa]",
                "-t", f"{duration:.3f}", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", str(output_video)]
        run_process(cmd)
        return output_video
