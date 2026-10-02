import re
import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger("ai_shorts.scenes")

class SceneDetector:
    @staticmethod
    def detect_scenes(video_path: Path, min_scene_len: float = 2.0) -> List[Dict[str, float]]:
        """
        Detects scene boundaries using local FFmpeg select filter.
        Falls back to adaptive uniform chunking if scene detection returns few cuts.
        """
        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-filter_complex", "select='gt(scene,0.3)',metadata=print:file=-",
            "-f", "null", "-"
        ]
        scene_times = [0.0]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=40.0)
            for line in res.stdout.splitlines():
                if "pts_time:" in line:
                    m = re.search(r"pts_time:([0-9.]+)", line)
                    if m:
                        t = float(m.group(1))
                        if t - scene_times[-1] >= min_scene_len:
                            scene_times.append(t)
        except Exception as e:
            logger.debug(f"Scene detection command skipped/timed out: {e}")

        # If too few scenes found, generate regular candidate segment boundaries
        from app.media.ffmpeg_core import FFmpegCore
        info = FFmpegCore.get_video_info(video_path)
        total_dur = info["duration"]

        if len(scene_times) <= 2 and total_dur > 6.0:
            step = 15.0 if total_dur > 60.0 else 5.0
            scene_times = [i * step for i in range(int(total_dur / step) + 1)]

        segments = []
        for i in range(len(scene_times) - 1):
            start = scene_times[i]
            end = min(total_dur if total_dur > 0 else scene_times[i+1], scene_times[i+1])
            if end - start >= 1.5:
                segments.append({"start": round(start, 2), "end": round(end, 2)})

        return segments
