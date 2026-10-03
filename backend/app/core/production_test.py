"""Production test: prove ClipRank can really make a Short on this machine.

The source is a ClipRank-generated test asset (synthetic picture plus synthesized
speech) so there is no third-party rights question. Each stage uses the production
code path, and the result names the exact component that failed. Success means a
real MP4 passed the delivery spec and objective QC.
"""
import asyncio
import json
import re
import shutil
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.core import config
from app.core.failures import redact
from app.core.runtime import run_blocking, run_process

OUTPUT_NAME = "cliprank-production-test.mp4"
# Plain words only: brand names defeat the strict speech-vs-text alignment check by design.
SPOKEN = ("This is a simple test of the whole system. The voice, the captions and the sound mix "
          "are all made and checked right now.")
NARRATION = "Watch how this test short comes together"
STEPS = ["Source ingestion", "Transcription", "AI analysis", "Narration (TTS)", "Captions", "FFmpeg render", "Final QC"]


def output_dir() -> Path:
    return config.OUTPUT_STORAGE_DIR / "diagnostics"


def report_path() -> Path:
    return output_dir() / "production-test.json"


def provenance() -> Dict[str, Any]:
    return {"source_url": "cliprank-generated:production-test", "source_type": "cliprank_generated", "creator": "ClipRank",
            "license": "original", "rights_status": "commercial_use_permitted", "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z']+", text.lower())


class ProductionTest:
    _state: Dict[str, Any] = {"status": "idle"}
    _task: Optional[asyncio.Task] = None

    @classmethod
    def status(cls) -> Dict[str, Any]:
        if cls._state.get("status") == "idle" and report_path().is_file():
            try:
                return {**json.loads(report_path().read_text()), "persisted": True}
            except ValueError:
                pass
        return cls._state

    @classmethod
    def start(cls) -> Dict[str, Any]:
        if cls._task and not cls._task.done():
            return cls._state
        cls._state = {"status": "running", "id": uuid.uuid4().hex[:10], "started": time.time(),
                      "steps": [{"name": name, "status": "pending"} for name in STEPS]}
        cls._task = asyncio.get_running_loop().create_task(cls._run(cls._state))
        return cls._state

    @classmethod
    async def run_to_completion(cls) -> Dict[str, Any]:
        """Used by the CLI and tests: same code path, awaited."""
        state = {"status": "running", "id": uuid.uuid4().hex[:10], "started": time.time(),
                 "steps": [{"name": name, "status": "pending"} for name in STEPS]}
        cls._state = state
        await cls._run(state)
        return state

    @classmethod
    async def _run(cls, state: Dict[str, Any]) -> None:
        workspace = config.TEMP_STORAGE_DIR / f"prodtest_{state['id']}"
        workspace.mkdir(parents=True, exist_ok=True)
        context: Dict[str, Any] = {"workspace": workspace}
        failed = False
        try:
            for index, (name, stage) in enumerate(zip(STEPS, [cls.ingest, cls.transcribe, cls.analyze, cls.narrate,
                                                              cls.captions, cls.render, cls.qc])):
                step = state["steps"][index]
                if failed:
                    step["status"] = "skipped"
                    continue
                step["status"], began = "running", time.time()
                try:
                    step["detail"] = await stage(context)
                    step["status"] = "passed"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failed = True
                    step.update(status="failed", detail=redact(str(exc))[-700:] or type(exc).__name__,
                                technical=redact(traceback.format_exc())[-3000:])
                step["seconds"] = round(time.time() - began, 1)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
            failing = next((s for s in state["steps"] if s["status"] == "failed"), None)
            state.update(status="done", finished=time.time(), ready=failing is None,
                         verdict="SYSTEM READY" if failing is None else f"NOT READY — {failing['name']} failed",
                         failed_component=failing["name"] if failing else None,
                         output=str(output_dir() / OUTPUT_NAME) if failing is None else None)
            output_dir().mkdir(parents=True, exist_ok=True)
            report_path().write_text(json.dumps(state, indent=2, default=str))

    # ----------------------------------------------------------------- stages
    @staticmethod
    async def ingest(ctx: Dict[str, Any]) -> str:
        from app.sources.ingestion import SourceIngestion
        from app.studio.policy import RightsPolicyEngine
        from app.tts.voice_engine import TTSEngine
        from app.core.database import get_settings
        workspace: Path = ctx["workspace"]
        voice = TTSEngine.validate_ready(get_settings().get("default_voice"))
        ctx["voice"] = voice
        speech = await TTSEngine.synthesize_timed(SPOKEN, workspace / "source_speech.wav", voice)
        seconds = max(13.0, speech["duration"] + 3.0)
        raw = workspace / "raw_source.mp4"
        await run_blocking(run_process, [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={seconds:.2f}",
            "-i", str(workspace / "source_speech.wav"), "-f", "lavfi", "-i",
            f"anoisesrc=color=pink:amplitude=0.03:sample_rate=48000:duration={seconds:.2f}",
            # A continuous ambient bed, as real footage has: the speech is not followed by dead silence.
            "-filter_complex", "[1:a]aresample=48000[v];[v][2:a]amix=inputs=2:duration=longest:normalize=0[a]",
            "-map", "0:v", "-map", "[a]", "-t", f"{seconds:.2f}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", str(raw)])
        source = await run_blocking(SourceIngestion.ingest_video_file, raw, workspace / "downloads" / "source.mp4")
        if not RightsPolicyEngine.evaluate([provenance()], "documented_permission")["passed"]:
            raise ValueError("The generated test asset did not pass the rights policy.")
        ctx.update(source=source, duration=seconds, expected_text=SPOKEN)
        return f"Generated and ingested a {seconds:.0f}s test asset with {speech['duration']:.1f}s of speech (rights: ClipRank-original)."

    @staticmethod
    async def transcribe(ctx: Dict[str, Any]) -> str:
        from app.media.ffmpeg_core import FFmpegCore
        from app.transcription.transcriber import Transcriber
        audio = await run_blocking(FFmpegCore.extract_audio, ctx["source"], ctx["workspace"] / "audio" / "source.wav")
        result = await run_blocking(Transcriber.transcribe, audio)
        if not result.get("available"):
            raise ValueError(result.get("warning") or "Speech transcription is unavailable.")
        heard, expected = set(_words(result["text"])), set(_words(ctx["expected_text"]))
        overlap = len(heard & expected) / max(1, len(expected))
        if overlap < .6 or not any(s.get("words") for s in result["segments"]):
            raise ValueError(f"Transcription heard {result['text']!r}; only {overlap:.0%} of the spoken words matched or word timing was missing.")
        ctx["transcript"] = result
        return f"Recognized {len(heard & expected)}/{len(expected)} spoken words with word-level timestamps."

    @staticmethod
    async def analyze(ctx: Dict[str, Any]) -> str:
        from PIL import Image, ImageDraw
        from app.ai.ranking_verifier import parse_object
        from app.ai.router import AIRouter
        from app.core.database import get_settings
        provider, name = await AIRouter.get_active_provider(get_settings())
        if not provider:
            raise ValueError("No AI vision provider is connected. Add a Gemini key or connect a local vision model in Settings.")
        image = Image.new("RGB", (500, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((35, 75, 185, 225), fill="red")
        draw.rectangle((235, 35, 310, 110), fill="blue")
        draw.rectangle((360, 190, 435, 265), fill="blue")
        path = ctx["workspace"] / "frames" / "vision.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        raw = await provider.analyze_images([path], 'Inspect the image. Return ONLY JSON: {"red_circles": <integer>, "blue_squares": <integer>}.')
        if not raw:
            raise ValueError(f"{name.title()} returned no answer: {getattr(provider, 'last_error', None) or 'check the key, model and network'}")
        answer = parse_object(raw)
        if answer.get("red_circles") != 1 or answer.get("blue_squares") != 2:
            raise ValueError(f"{name.title()} misread a known test image: {answer}")
        return f"{name.title()} read a known test image correctly."

    @staticmethod
    async def narrate(ctx: Dict[str, Any]) -> str:
        from app.tts.voice_engine import TTSEngine
        voice_file = ctx["workspace"] / "voice" / "narration.wav"
        speech = await TTSEngine.synthesize_timed(NARRATION, voice_file, ctx["voice"])
        if speech["duration"] < .8 or not speech["words"]:
            raise ValueError("Narration audio is empty or has no word timings.")
        ctx.update(voice_file=voice_file, speech=speech)
        return f"Synthesized {speech['duration']:.1f}s of narration with {len(speech['words'])} measured word timings ({ctx['voice']})."

    @staticmethod
    async def captions(ctx: Dict[str, Any]) -> str:
        from app.media.captions import CaptionRenderer
        from app.media.production_qc import ProductionQC
        speech = ctx["speech"]
        ProductionQC.validate_words(speech["words"], speech["duration"] + .05, NARRATION)
        ass = await run_blocking(CaptionRenderer.write_ass, ctx["workspace"] / "captions.ass", speech["segments"], 0, ctx["duration"])
        if not ass:
            raise ValueError("No caption file was produced.")
        ctx["ass"] = ass
        ctx["hook"] = await run_blocking(CaptionRenderer.render_overlay_card, ctx["workspace"] / "hook.png", "Production test")
        return "Word-timed caption track and hook card created."

    @staticmethod
    async def render(ctx: Dict[str, Any]) -> str:
        from app.media.production_qc import ProductionQC
        from app.media.reframer import VideoReframer
        folder = ctx["workspace"] / "renders"
        folder.mkdir(parents=True, exist_ok=True)
        duration = min(12.0, ctx["duration"])
        segment = await run_blocking(VideoReframer.reframe_to_vertical, ctx["source"], folder / "segment.mp4", start=0,
                                     duration=duration, overlay_png=ctx["hook"], captions_ass=ctx["ass"],
                                     narration_wav=ctx["voice_file"], layout="fit")
        final = await run_blocking(ProductionQC.master_audio, segment, folder / OUTPUT_NAME, {})
        ctx.update(final=final, final_duration=duration)
        return f"Rendered a {duration:.0f}s 1080×1920 Short and mastered its audio."

    @staticmethod
    async def qc(ctx: Dict[str, Any]) -> str:
        from app.media.production_qc import ProductionQC
        from app.media.verify import verify_mp4
        report = await run_blocking(verify_mp4, ctx["final"], ctx["final_duration"])
        timeline = [{"timeline_start": 0, "duration": ctx["final_duration"], "speech": ctx["speech"], "payoff_relative": None}]
        await run_blocking(ProductionQC.inspect, ctx["final"], ctx["final_duration"], ctx["workspace"] / "qc", timeline, True, True)
        output_dir().mkdir(parents=True, exist_ok=True)
        shutil.copy2(ctx["final"], output_dir() / OUTPUT_NAME)
        return (f"{report['width']}×{report['height']} {report['video_codec']}/{report['audio_codec']}, {report['duration']:.1f}s, "
                f"{report['frame_count']} frames; no black/frozen frames, audio clean, narration audible.")


def main() -> int:
    """CLI: PYTHONPATH=backend .venv/bin/python -m app.core.production_test"""
    from app.core.database import init_db
    init_db()
    state = asyncio.run(ProductionTest.run_to_completion())
    for step in state["steps"]:
        print(f"  [{step['status']:>7}] {step['name']}: {step.get('detail', '')}")
    print(state["verdict"], "->", state.get("output") or "")
    return 0 if state["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
