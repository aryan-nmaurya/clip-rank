import shutil
from pathlib import Path
from app.core.runtime import run_process
from app.media.ffmpeg_core import FFmpegCore


class TTSEngine:
    @staticmethod
    def synthesize(text: str, output_wav: Path, voice: str = "Samantha") -> float:
        if not shutil.which("say"):
            raise ValueError("Narration requires macOS speech synthesis. Turn narration off to preserve source audio.")
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        aiff = output_wav.with_suffix(".aiff")
        try:
            run_process(["say", "-v", voice if voice and voice != "Auto" else "Samantha", "-r", "185", "-o", str(aiff), text], timeout=60)
            run_process(["ffmpeg", "-v", "error", "-y", "-i", str(aiff), "-ar", "44100", "-ac", "1", str(output_wav)], timeout=60)
            duration = FFmpegCore.get_video_info(output_wav)["duration"]
            if duration < .2 or output_wav.stat().st_size < 100:
                output_wav.unlink(missing_ok=True)
                raise ValueError("Speech synthesis produced empty audio. Check macOS voice availability or disable narration.")
            return duration
        finally:
            aiff.unlink(missing_ok=True)
