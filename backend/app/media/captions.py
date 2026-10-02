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
    def render_production_card(output_png, topic, rank, label, count, width=VIDEO_WIDTH, height=VIDEO_HEIGHT):
        output_png.parent.mkdir(parents=True,exist_ok=True)
        image = Image.new('RGBA',(width,height),(0,0,0,0))
        draw = ImageDraw.Draw(image)
        title = f'{count} {topic}'.upper()
        size = 64
        while size > 32:
            face = font(size,True)
            if draw.textlength(title,font=face) <= width-180:
                break
            size -= 2
        lines = wrap(draw,title,face,width-180)
        if len(lines) > 2:
            raise ValueError('Topic title does not fit safely in the production header.')
        for idx,line in enumerate(lines):
            draw.text((width/2,85+idx*66),line,font=face,anchor='mm',fill='white',stroke_width=3,stroke_fill='black')
        y = 200 if len(lines)>1 else 166
        draw.rounded_rectangle((80,y-34,200,y+34),radius=18,fill='#ffe14a')
        draw.text((140,y),f'#{rank}',font=font(51,True),anchor='mm',fill='#18181b')
        label_face = font(36)
        if draw.textlength(label,font=label_face) > width-330:
            label_face = font(30)
        if draw.textlength(label,font=label_face) > width-330:
            raise ValueError('Rank label does not fit inside the mobile safe area.')
        draw.text((225,y),label,font=label_face,anchor='lm',fill='white',stroke_width=2,stroke_fill='black')
        image.save(output_png)
        return output_png

    @staticmethod
    def render_overlay_card(output_png: Path, title: str, caption_text: str = "",
                            rank=None, viral_score=None, width=VIDEO_WIDTH, height=VIDEO_HEIGHT,
                            ranking_items=None) -> Path:
        output_png.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        if ranking_items:
            # Same hierarchy as the reference: bold header + persistent colored list.
            title = "RANKING " + " ".join(title.split()).upper()
            size = 58
            while size > 18:
                face = font(size, True)
                lines = wrap(draw, title, face, width - 48)
                if len(lines) <= 3 and len(lines) * (size + 8) <= 130:
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
                    label = item.get("label") or item.get("title", "Highlight")
                    label_size = 28 if active else 23
                    face = font(label_size)
                    while draw.textlength(label, face) > width - 120 and label_size > 16:
                        label_size -= 1
                        face = font(label_size)
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
                raise ValueError('Speech exists without actual word timestamps; estimated captions are not production-ready.')
            groups, group = [], []
            for word in words:
                if group and (len(group)>=3 or word['start']-group[-1]['end']>.25 or len(' '.join(w['word'] for w in [*group,word]))>25):
                    groups.append(group); group=[]
                group.append(word)
            if group: groups.append(group)
            for group in groups:
                start = max(0,group[0]['start']-clip_start)
                end = min(clip_end-clip_start,group[-1]['end']-clip_start)
                if end <= start: continue
                emphasis = next((idx for idx,w in enumerate(group) if re.search(r'fail|wait|impossible|perfect|landing',w['word'],re.I)),None)
                text = ' '.join((r'{\c&H004AE1FF&}' if idx==emphasis else r'{\c&H00FFFFFF&}')+safe(w['word']) for idx,w in enumerate(group))
                events.append(f'Dialogue: 0,{stamp(start)},{stamp(end)},Caption,,0,0,0,,{text}')
        if not events:
            return None
        header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,72,&H00FFFFFF,&H004AE1FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,1,2,85,150,360,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(header + "\n".join(events) + "\n")
        return output_path

    @staticmethod
    def render_ass_track(ass_path: Path, duration: float, width=VIDEO_WIDTH, height=VIDEO_HEIGHT):
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
            tagged = [(color, word) for color, run in
                      re.findall(r"\{\\c&H00([A-F0-9]{6})&\}([^{}]+)", fields[9])
                      for word in run.split()]
            face = font(round(width*.067))
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            text = " ".join(word.strip() for _, word in tagged)
            # Long technology names need two readable lines rather than tiny
            # type or a clipped one-line caption. Timing remains unchanged.
            while True:
                lines=[[]]
                for color,word in tagged:
                    trial=' '.join(w for _,w in [*lines[-1],(color,word)])
                    if lines[-1] and draw.textlength(trial,font=face)>width-235: lines.append([])
                    lines[-1].append((color,word))
                fits=len(lines)<=2 and all(draw.textlength(' '.join(w for _,w in parts),font=face)<=width-235 for parts in lines)
                if fits or face.size<=54: break
                face=font(face.size-2)
            if not fits: raise ValueError('Caption cannot fit legibly inside the mobile safe area.')
            if len(lines)>2: raise ValueError('Caption needs more than two mobile-safe lines.')
            for row,parts in enumerate(lines):
                line=' '.join(w for _,w in parts)
                x=(width-65-draw.textlength(line,font=face))/2
                for color,word in parts:
                    rgb=tuple(int(color[i:i+2],16) for i in (4,2,0))
                    draw.text((x,round(height*.74)+row*(face.size+14)),word,font=face,fill=rgb,stroke_width=6,stroke_fill='black')
                    x+=draw.textlength(word+' ',font=face)
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
