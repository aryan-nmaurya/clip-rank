"""Inspect source-wide frames for existing countdown graphics before selection."""
import csv
import hashlib
import io
import json
import shutil
import sys
import threading
from pathlib import Path
from PIL import Image, ImageDraw
from app.core.config import STORAGE_DIR
from app.core.runtime import run_process, check_cancelled
from app.media.ffmpeg_core import FFmpegCore
from app.sources.ranking_policy import RankingSourcePolicy

_compile_lock = threading.Lock()


class SourceScreening:
    @staticmethod
    def read_text(images):
        if sys.platform == "darwin" and shutil.which("swiftc"):
            source = Path(__file__).with_name("native_ocr.swift")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
            folder = STORAGE_DIR / "tools"
            folder.mkdir(parents=True, exist_ok=True)
            binary = folder / f"video-ocr-{digest}"
            with _compile_lock:
                check_cancelled()
                if not binary.is_file():
                    run_process(["swiftc", "-module-cache-path", str(folder / "swift-cache"),
                                 str(source), "-o", str(binary)], timeout=180)
            return json.loads(run_process([str(binary), *map(str, images)], timeout=120))
        if shutil.which("tesseract"):
            result = []
            for path in images:
                with Image.open(path) as image:
                    width, height = image.size
                text = run_process(["tesseract", str(path), "stdout", "--psm", "11", "tsv"], timeout=30).decode()
                lines = {}
                for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
                    if not row["text"].strip() or float(row["conf"]) < 35:
                        continue
                    key = (row["block_num"], row["par_num"], row["line_num"])
                    line = lines.setdefault(key, {"text": "", "x": int(row["left"])/width, "y": int(row["top"])/height})
                    line["text"] += " " + row["text"]
                result.append(list(lines.values()))
            return result
        return None

    @classmethod
    def inspect(cls, source, folder):
        folder.mkdir(parents=True, exist_ok=True)
        duration = source["duration"]
        sample_count = min(24, max(4, int(duration / 3) + 1))
        timestamps = [.05 + (duration - .15) * i / (sample_count - 1) for i in range(sample_count)]
        images = []
        for idx, timestamp in enumerate(timestamps):
            check_cancelled()
            path = folder / f"frame_{idx}.jpg"
            FFmpegCore.extract_frame(Path(source["file_path"]), path, timestamp)
            images.append(path)
        try:
            blocks = cls.read_text(images)
        except InterruptedError:
            raise
        except (RuntimeError, TimeoutError, ValueError):
            blocks = None  # A vision model must verify the source if OCR failed.
        reason = RankingSourcePolicy.overlay_reason(blocks) if blocks is not None else None
        sheet = Image.new("RGB", (900, ((len(images) + 2)//3) * 420), "#111111")
        draw = ImageDraw.Draw(sheet)
        for idx, path in enumerate(images):
            x, y = (idx % 3) * 300, (idx // 3) * 420
            with Image.open(path) as frame:
                frame.thumbnail((296, 392))
                sheet.paste(frame, (x+(300-frame.width)//2, y+22))
            draw.text((x+5, y+4), f"{timestamps[idx]:.1f}s", fill="white")
        sheet_path = folder / "source_sheet.jpg"
        sheet.save(sheet_path)
        return {"rejected": bool(reason), "reason": reason, "ocr_available": blocks is not None,
                "sample_count": len(images), "sheet": sheet_path,
                "method": "Frame OCR" if blocks is not None else "Awaiting vision verification"}
