"""Find moments using measured frames, then allow AI to reason over real evidence."""
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps
from app.core.runtime import run_process
from app.media.ffmpeg_core import FFmpegCore
from app.media.captions import font


class MomentAnalyzer:
    @staticmethod
    def analyze(video: Path, count=3, target_duration=25.0, segments=None, title="Source video", coverage=False, minimum_duration=6.):
        info = FFmpegCore.get_video_info(video)
        duration = info["duration"]
        if duration < 1:
            raise ValueError("No playable source footage.")
        # Bound memory on long videos while retaining samples across the entire source.
        fps = min(2.0, 1800 / duration)
        raw = run_process(["ffmpeg", "-v", "error", "-i", str(video), "-an", "-vf",
                           f"fps={fps},scale=160:90", "-pix_fmt", "gray", "-f", "rawvideo", "-"], timeout=600)
        frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 90, 160).astype(np.float32)
        if not len(frames):
            raise ValueError("Source frames could not be decoded.")
        differences = np.zeros(len(frames))
        differences[1:] = np.mean(np.abs(frames[1:] - frames[:-1]), axis=(1, 2)) / 255
        contrast = np.std(frames, axis=(1, 2)) / 80
        exposure = np.mean((frames > 12) & (frames < 245), axis=(1, 2))
        if duration<minimum_duration:raise ValueError('Source is too short for a complete Short longer than 10 seconds.')
        desired = min(max(target_duration,minimum_duration), duration)
        # Return fewer clips when the source cannot support distinct complete moments.
        if not coverage:
            desired = min(desired, max(minimum_duration, duration / min(count, max(1, int(duration // minimum_duration)))))
        step = max(1, desired / 4)
        starts = list(np.arange(0, max(0, duration - desired) + .01, step))
        starts.append(max(0, duration - desired))
        candidates = []
        for start in sorted(set(starts)):
            end = min(duration, start + desired)
            original_start,original_end=start,end
            # Start/end on real sentence boundaries when close to the proposed window.
            if segments:
                nearest = min(segments, key=lambda s: abs(s["start"] - start))
                if abs(nearest["start"] - start) < 3:
                    start = nearest["start"]
                near_end = [s["end"] for s in segments if s["end"] > start + min(5, desired)]
                if near_end:
                    boundary = min(near_end, key=lambda t: abs(t - end))
                    if abs(boundary - end) < 4:
                        end = min(duration, boundary)
                # Speech boundaries cannot shrink a complete visual story below
                # the required output length; keep its real context/aftermath.
                if end-start<minimum_duration:start,end=original_start,original_end
            lo = min(len(frames) - 1, int(start * fps))
            if end-start<minimum_duration:continue
            hi = max(lo + 1, min(len(frames), int(end * fps)))
            measured = differences[lo:hi]
            # A hard scene cut is not subject movement; avoid inflating static meme scores.
            motion = float(np.mean(np.where(measured > .30, 0, measured)))
            quality = float(np.clip(np.mean(contrast[lo:hi]) * .5 + np.mean(exposure[lo:hi]) * .5, 0, 1))
            peak = lo + int(np.argmax(differences[lo:hi]))
            score = round(min(90, 30 + quality * 30 + min(1, motion / .12) * 25))
            candidates.append({"start": round(float(start), 3), "end": round(float(end), 3),
                               "peak": round(min(duration - .1, peak / fps), 3), "score": score,
                               "motion": round(motion, 4), "clarity": round(quality, 3),
                               "title": title, "reason": f"Visual estimate: motion {motion:.3f}, clarity {quality:.2f}; {end-start:.1f}s of source footage.",
                               "analysis_basis": "visual metrics"})
        if coverage:
            # Include the entire timeline, not just the busiest frames; failures can follow quiet preparation.
            indices = np.linspace(0, len(candidates)-1, min(count, len(candidates))).round().astype(int)
            return [candidates[i] for i in sorted(set(indices))]
        selected = []
        for item in sorted(candidates, key=lambda x: x["score"], reverse=True):
            if any(min(item['end'],s['end'])-max(item['start'],s['start'])>.05 for s in selected):
                continue
            selected.append(item)
            if len(selected) == count:
                break
        return selected

    @staticmethod
    def contact_sheet(video: Path, moments, output: Path, dense=False, layout=None):
        output.parent.mkdir(parents=True, exist_ok=True)
        fractions = (.01, .1, .2, .3, .4, .5, .6, .7, .8, .9, .99) if dense else (.15, .5, .85)
        cell_w, cell_h = (240, 340) if dense else (200, 148)
        last_frame=max(0,FFmpegCore.get_video_info(video)['duration']-.1)
        sheet = Image.new("RGB", (cell_w * len(fractions), len(moments) * cell_h), "#18181b")
        draw = ImageDraw.Draw(sheet)
        for i, moment in enumerate(moments):
            draw.text((6, i * cell_h + 4), f"ID {i}  {moment['start']:.1f}–{moment['end']:.1f}s", font=font(14), fill="white")
            for j, fraction in enumerate(fractions):
                timestamp = min(last_frame,moment["start"] + (moment["end"] - moment["start"]) * fraction)
                jpg = output.parent / f"{output.stem}_{i}_{j}.jpg"
                FFmpegCore.extract_frame(video, jpg, timestamp)
                with Image.open(jpg) as frame:
                    if layout == "fill":
                        # Match the actual center crop between ranking title/footer bands.
                        frame = ImageOps.fit(frame, (240, 325), centering=(.5, .5))
                    frame.thumbnail((cell_w - 4, cell_h - 38))
                    sheet.paste(frame, (j * cell_w + (cell_w - frame.width) // 2, i * cell_h + 30))
                jpg.unlink(missing_ok=True)
                draw.text((j * cell_w + 5, i * cell_h + cell_h - 20), f'{timestamp:.2f}s', font=font(14), fill='white')
        sheet.save(output)
        return output

    @staticmethod
    def clamp_selections(moments, duration, count):
        clean = []
        for item in moments:
            try:
                raw_start, raw_end = float(item["start"]), float(item["end"])
                if not np.isfinite(raw_start) or not np.isfinite(raw_end):
                    continue
                start = max(0., raw_start)
                end = min(duration, raw_end)
                if end - start < 1:
                    continue
                if any(max(0, min(end, s["end"]) - max(start, s["start"])) > .3 * min(end-start, s["end"]-s["start"]) for s in clean):
                    continue
                score = max(0, min(100, int(item.get("viral_score", item.get("score", 50)))))
                clean.append({**item, "start": start, "end": end, "score": score})
            except (TypeError, ValueError, KeyError, OverflowError):
                continue
            if len(clean) >= count:
                break
        return clean
