import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from app.core.config import VIDEO_WIDTH, VIDEO_HEIGHT


def font(size, condensed=False):
    choices = (["/System/Library/Fonts/Supplemental/Impact.ttf"] if condensed else []) + [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    for path in choices:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def wrap(draw, text, face, max_width):
    lines = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=face) > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def clean_label(text, max_words=5):
    text = re.sub(r"#[\w]+|https?://\S+", "", text)
    return " ".join(text.split()[:max_words]).strip(" -|.,") or "Highlight"


class CaptionRenderer:
    @staticmethod
    def render_overlay_card(output_png: Path, title: str, caption_text: str = "",
                            rank=None, viral_score=None, width=VIDEO_WIDTH, height=VIDEO_HEIGHT,
                            ranking_items=None) -> Path:
        output_png.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        if ranking_items:
            # Same hierarchy as the reference: bold header + persistent colored list.
            title = "RANKING " + clean_label(title, 12).upper()
            size = 58
            while size > 28:
                face = font(size, True)
                lines = wrap(draw, title, face, width - 48)
                if len(lines) <= 2:
                    break
                size -= 2
            line_height = size + 8
            y = 70 + (110 - len(lines) * line_height) // 2
            for line in lines:
                words = line.split()
                x = (width - draw.textlength(line, font=face)) / 2
                for j, word in enumerate(words):
                    color = "#ffe14a" if j == len(words) - 1 and line == lines[-1] else "white"
                    draw.text((x, y), word, font=face, fill=color, stroke_width=3, stroke_fill="black")
                    x += draw.textlength(word + " ", font=face)
                y += line_height
            colors = ["#eaff4b", "#ffffff", "#ff5151", "#7aff69", "#f9a1d9", "#78caff"]
            row_step = min(104, 820 // len(ranking_items))
            for item in sorted(ranking_items, key=lambda x: x["assigned_rank"]):
                r = item["assigned_rank"]
                y = 270 + (r - 1) * row_step
                color = colors[(r - 1) % len(colors)]
                active = r == rank
                draw.text((28, y), f"{r}.", font=font(64 if active else 56, True), fill=color,
                          stroke_width=4, stroke_fill="black")
                if r >= rank:
                    label = clean_label(item.get("label") or item.get("title", "Highlight"), 4)
                    face = font(28 if active else 23)
                    label = label[:32]
                    draw.text((95, y + 24), label, font=face, fill="white", stroke_width=3, stroke_fill="black")
            if caption_text:
                face = font(28)
                text = clean_label(caption_text, 9)
                draw.text((width / 2, height - 82), text, font=face, anchor="mm", fill="white",
                          stroke_width=2, stroke_fill="black")
        else:
            # Hook is brief and disappears via the overlay enable expression in the renderer.
            face = font(48, True)
            lines = wrap(draw, clean_label(title, 9).upper(), face, width - 100)[:2]
            for i, line in enumerate(lines):
                draw.text((width / 2, 105 + i * 58), line, font=face, anchor="mt",
                          fill="#ffe14a" if i == len(lines) - 1 else "white",
                          stroke_width=4, stroke_fill="black")
        image.save(output_png)
        return output_png

    @staticmethod
    def write_ass(output_path: Path, segments, clip_start: float, clip_end: float):
        """Burn phrase captions with actual word timestamps; highlight the spoken word."""
        def stamp(value):
            centiseconds = round(max(0, value) * 100)
            return f"{centiseconds // 360000}:{centiseconds // 6000 % 60:02}:{centiseconds // 100 % 60:02}.{centiseconds % 100:02}"

        def safe(text):
            return re.sub(r"[{}\\\r\n]", "", text).upper()

        events = []
        for segment in segments:
            words = [w for w in segment.get("words", [])
                     if w["start"] >= clip_start - .03 and w["end"] <= clip_end + .03]
            if not words and segment["start"] >= clip_start and segment["end"] <= clip_end:
                texts = segment["text"].split()
                length = (segment["end"] - segment["start"]) / max(1, len(texts))
                words = [{"word": w, "start": segment["start"] + i * length,
                          "end": segment["start"] + (i + 1) * length} for i, w in enumerate(texts)]
            for i in range(0, len(words), 3):
                group = words[i:i + 3]
                for j, word in enumerate(group):
                    start = max(0, word["start"] - clip_start)
                    end = min(clip_end - clip_start, group[j + 1]["start"] - clip_start if j + 1 < len(group) else word["end"] - clip_start)
                    if end <= start:
                        continue
                    text = " ".join((r"{\c&H004AE1FF&}" if k == j else r"{\c&H00FFFFFF&}") + safe(w["word"]) for k, w in enumerate(group))
                    events.append(f"Dialogue: 0,{stamp(start)},{stamp(end)},Caption,,0,0,0,,{text}")
        if not events:
            return None
        header = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,48,&H00FFFFFF,&H004AE1FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,60,95,220,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(header + "\n".join(events) + "\n")
        return output_path

    @staticmethod
    def render_ass_track(ass_path: Path, duration: float, width=720, height=1280):
        """Pillow caption track: works even when FFmpeg was built without libass."""
        from app.core.runtime import run_process, check_cancelled
        folder = ass_path.parent / (ass_path.stem + "_frames")
        folder.mkdir(parents=True, exist_ok=True)
        blank = folder / "blank.png"
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(blank)
        entries = []
        current = 0.
        def seconds(stamp):
            h, m, s = stamp.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        for index, line in enumerate(ass_path.read_text().splitlines()):
            check_cancelled()
            if not line.startswith("Dialogue:"):
                continue
            fields = line.split(",", 9)
            start, end = seconds(fields[1]), min(duration, seconds(fields[2]))
            if end <= start:
                continue
            if start > current:
                entries.append((blank, start-current))
            tagged = re.findall(r"\{\\c&H00([A-F0-9]{6})&\}([^{}]+)", fields[9])
            face = font(48)
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            text = " ".join(word.strip() for _, word in tagged)
            while draw.textlength(text, font=face) > width - 110 and face.size > 26:
                face = font(face.size - 2)
            x = (width - draw.textlength(text, font=face)) / 2
            for color, word in tagged:
                rgb = tuple(int(color[i:i+2], 16) for i in (4, 2, 0))
                draw.text((x, height-285), word.strip(), font=face, fill=rgb, stroke_width=4, stroke_fill="black")
                x += draw.textlength(word.strip() + " ", font=face)
            path = folder / f"caption_{index}.png"
            image.save(path)
            entries.append((path, end-start))
            current = end
        if current < duration:
            entries.append((blank, duration-current))
        entries.append((blank, .04))
        listing = folder / "frames.txt"
        listing.write_text("".join("file '" + str(p.resolve()).replace("'", "'\\''") + f"'\nduration {d:.4f}\n" for p, d in entries))
        track = ass_path.with_suffix(".mov")
        run_process(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                     "-vf", "fps=30", "-c:v", "qtrle", "-pix_fmt", "argb", "-t", str(duration), str(track)])
        return track
