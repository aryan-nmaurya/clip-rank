"""Reject existing rankings before extracting any footage from them."""
import re


class RankingSourcePolicy:
    PATTERN = re.compile(
        r"\b(?:rank(?:ing|ings|ed)|count\s*down|compilation|tier\s*list)\b|"
        r"\btop\s*(?:\d+|three|four|five|six|seven|eight|nine|ten)\b|"
        r"\b(?:best|top)\b.{0,40}\b(?:moments|clips|fails|videos)\b", re.I)

    @classmethod
    def metadata_reason(cls, metadata):
        text = " ".join([str(metadata.get("title") or ""), *[str(t) for t in (metadata.get("tags") or [])]])
        if cls.PATTERN.search(text.replace("_", " ")):
            return "Title or tags identify an existing ranking/countdown/compilation."
        return None

    @classmethod
    def overlay_reason(cls, frames):
        """OCR evidence: ranking headings, aligned lists, or changing rank badges."""
        rank_badges = set()
        for blocks in frames:
            text = " ".join(b["text"] for b in blocks)
            if cls.PATTERN.search(text):
                return "Ranking/countdown text is already embedded in the footage."
            rows = []
            for block in blocks:
                label = block["text"].strip()
                match = re.match(r"^(?:#\s*)?(10|[1-9])(?:\s*[.)\-:]\s*|\s+)(.*)$", label)
                bare = re.fullmatch(r"#?\s*(10|[1-9])\s*[.)]?", label)
                if (match or bare) and block.get("x", 1) < .45:
                    rank = int((match or bare).group(1))
                    rows.append((rank, block.get("x", 0), block.get("y", 0), bool(match and match.group(2))))
                badge = re.fullmatch(r"(?:#\s*|number\s+|no\.?\s*)(10|[1-9])[.!]?", label, re.I)
                if badge:
                    rank_badges.add(int(badge.group(1)))
            for anchor in rows:
                aligned = [row for row in rows if abs(row[1] - anchor[1]) < .13]
                ranks = sorted(set(row[0] for row in aligned))
                if len(ranks) >= 3 or (len(ranks) >= 2 and ranks[-1] - ranks[0] == len(ranks) - 1 and any(r[3] for r in aligned)):
                    if len(set(round(r[2], 2) for r in aligned)) >= 2:
                        return "A numbered ranking list is already embedded in the footage."
        if len(rank_badges) >= 2:
            return "Changing countdown numbers are already embedded in the footage."
        return None
