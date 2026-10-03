from pathlib import Path
import math
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
                            reveal_sfx: Path = None, narration_offset: float = 0,
                            protected_regions=None, music_wav: Path = None,
                            framing=None, hook_duration: float = 3,
                            credit_png: Path = None) -> Path:
        info = FFmpegCore.get_video_info(input_video)
        if not info["has_video"] or start < 0 or start >= info["duration"]:
            raise ValueError("Selected moment is outside the source video.")
        duration = min(duration, info["duration"] - start)
        if duration < .5:
            raise ValueError("Selected moment is too short to render.")
        if type(narration_offset) not in (int,float) or not math.isfinite(narration_offset) or not 0<=narration_offset<duration:
            raise ValueError('Invalid narration offset.')
        protected_regions=protected_regions or []
        for region in protected_regions:
            if any(type(region.get(k)) not in (int,float) or not math.isfinite(region[k]) for k in ('start','end')) or not 0<=region['start']<region['end']<=duration:
                raise ValueError('Invalid protected dialogue region.')
        if type(hook_duration) not in (int,float) or not math.isfinite(hook_duration) or not 0<=hook_duration<=5:
            raise ValueError('Invalid hook display duration.')
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
        music_index=None
        if music_wav:
            music_index=next_input;next_input+=1
            cmd+=['-i',str(music_wav)]
        credit_index=None
        if credit_png:
            credit_index=next_input;next_input+=1
            cmd+=['-loop','1','-i',str(credit_png)]
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
        if framing and framing.get('layout')=='tracked':
            crop_width=framing['crop_width'];points=framing['points']
            if type(crop_width) is not int or not 1<=crop_width<=info['width'] or not 1<=len(points)<=32:
                raise ValueError('Invalid measured crop width/track.')
            prior=-1
            for point in points:
                if any(type(point.get(k)) not in (int,float) or not math.isfinite(point[k]) for k in ('time','x')) or not prior<point['time']<=duration or not 0<=point['x']<=info['width']-crop_width:
                    raise ValueError('Invalid measured subject track.')
                prior=point['time']
            expression=f"{points[-1]['x']:.2f}"
            for left,right in reversed(list(zip(points,points[1:]))):
                if right.get('cut') is True:
                    expression=f"if(lt(t,{right['time']:.3f}),{left['x']:.2f},{expression})"
                else:
                    expression=(f"if(lt(t,{right['time']:.3f}),{left['x']:.2f}+({right['x']-left['x']:.2f})*"
                        f"max(0,t-{left['time']:.3f})/{right['time']-left['time']:.3f},{expression})")
            filters=[f"[0:v]setpts=PTS-STARTPTS,crop={crop_width}:{info['height']}:x='{expression}':y=0,scale={width}:{canvas_h}[frame]"]
        elif layout == "fit":
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
            overlay_enable = "" if title_band else f":enable='lt(t,{hook_duration:.3f})'"
            filters.append(f"[{visual}][{overlay_index}:v]overlay=0:0:shortest=1{overlay_enable}[decorated]")
            visual = "decorated"
        if caption_index is not None:
            filters.append(f"[{visual}][{caption_index}:v]overlay=0:0:shortest=1[captioned]")
            visual = "captioned"
        if credit_index is not None:
            filters.append(f'[{visual}][{credit_index}:v]overlay=0:0:shortest=1[credited]')
            visual='credited'
        audio_input = "0:a" if info["has_audio"] else f"{silent_index}:a"
        filters.append(f"[{audio_input}]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,afade=t=in:d=0.025,afade=t=out:st={max(0,duration-.04):.3f}:d=0.04,apad[original]")
        if narration_index is not None:
            voice_duration = FFmpegCore.get_video_info(narration_wav)["duration"]
            voice_end=narration_offset+voice_duration
            if ((narration_offset or protected_regions) and voice_end>duration-.1) or any(narration_offset<r['end']+.1 and voice_end>r['start']-.1 for r in protected_regions):
                raise ValueError('Narration would interrupt protected dialogue or the ending.')
            # Smooth restoration over 300 ms. Natural source impacts/reactions remain audible.
            filters.append(f"[original]volume='if(lt(t,{narration_offset:.3f}),1,if(lt(t,{voice_end:.3f}),0.18,if(lt(t,{voice_end+.3:.3f}),0.18+0.82*(t-{voice_end:.3f})/0.3,1)))':eval=frame[quiet]")
            filters.append(f"[{narration_index}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,afade=t=out:st={max(0,voice_duration-.025):.3f}:d=0.025,adelay={round(narration_offset*1000)}:all=1,apad[voice]")
            filters.append("[quiet][voice]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            filters.append("[original]anull[mix]")
        if sfx_index is not None:
            filters.append(f'[{sfx_index}:a]aresample=48000,aformat=channel_layouts=stereo,volume=.18,apad[sfx]')
            filters.append('[mix][sfx]amix=inputs=2:duration=first:normalize=0[effects]')
            mixed = 'effects'
        else:
            mixed = 'mix'
        if music_index is not None:
            # Source effects remain; narration/actor dialogue always has priority.
            protections='+'.join(f'between(t,{r["start"]:.3f},{r["end"]:.3f})' for r in protected_regions)
            if narration_index is not None:protections+=('+' if protections else '')+f'between(t,{narration_offset:.3f},{voice_end:.3f})'
            gain=f'if(gt({protections},0),0.06,0.30)' if protections else '0.60'
            filters.append(f"[{music_index}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,volume='{gain}':eval=frame,afade=t=in:d=0.12,afade=t=out:st={max(0,duration-.35):.3f}:d=0.35,apad[music]")
            filters.append(f'[{mixed}][music]amix=inputs=2:duration=first:normalize=0[scored]')
            mixed='scored'
        filters.append(f'[{mixed}]alimiter=limit=0.89:level=false:latency=true[outa]')
        cmd += ["-filter_complex", ";".join(filters), "-map", f"[{visual}]", "-map", "[outa]",
                "-t", f"{duration:.3f}", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", str(output_video)]
        run_process(cmd)
        return output_video
