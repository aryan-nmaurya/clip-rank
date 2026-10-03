"""Ranking topic catalog and breadth classifier.

A *broad* topic names a subject and lets any impressive visible moment qualify ("Insane Parkour Moments").
A *narrow* topic also demands a specific outcome ("Craziest Parkour Saves" needs a visible recovery), so far
fewer source clips can pass strict verification. Broad topics yield more verified clips per AI call; narrow
ones make a sharper video when enough footage exists.
"""
import re
from typing import Dict, List, Optional

BROAD: List[Dict[str, str]] = [
    {"topic": "Insane Parkour Moments", "pillar": "parkour"},
    {"topic": "Incredible Football Skills", "pillar": "sports"},
    {"topic": "Crazy Skateboarding Tricks", "pillar": "extreme"},
    {"topic": "Unbelievable Basketball Plays", "pillar": "sports"},
    {"topic": "Epic BMX Stunts", "pillar": "extreme"},
    {"topic": "Amazing Trampoline Flips", "pillar": "extreme"},
    {"topic": "Wild Surfing Moments", "pillar": "extreme"},
    {"topic": "Impressive Gymnastics Moves", "pillar": "sports"},
    {"topic": "Insane Freerunning Moments", "pillar": "parkour"},
    {"topic": "Crazy Snowboard Tricks", "pillar": "extreme"},
    {"topic": "Unbelievable Trick Shots", "pillar": "trick shots"},
    {"topic": "Incredible Soccer Goals", "pillar": "sports"},
    {"topic": "Epic Skiing Moments", "pillar": "extreme"},
    {"topic": "Amazing Rock Climbing Moves", "pillar": "extreme"},
    {"topic": "Funny Sports Moments", "pillar": "fails"},
    {"topic": "Crazy Motorcycle Stunts", "pillar": "stunts"},
    {"topic": "Impressive Martial Arts Moments", "pillar": "sports"},
    {"topic": "Insane Cliff Diving Moments", "pillar": "extreme"},
    {"topic": "Wild Mountain Biking Moments", "pillar": "extreme"},
    {"topic": "Amazing Street Basketball Handles", "pillar": "sports"},
]

NARROW: List[Dict[str, str]] = [
    {"topic": "Craziest Parkour Saves", "pillar": "parkour"},
    {"topic": "Impossible Goalkeeper Saves", "pillar": "sports"},
    {"topic": "Luckiest Near Misses Caught on Camera", "pillar": "near misses"},
    {"topic": "Parkour Fails", "pillar": "fails"},
    {"topic": "Skateboard Last-Second Recoveries", "pillar": "extreme"},
    {"topic": "Basketball Buzzer Beaters", "pillar": "sports"},
    {"topic": "Football Bicycle Kick Goals", "pillar": "sports"},
    {"topic": "BMX Landing Saves", "pillar": "extreme"},
    {"topic": "Snowboard Crash Recoveries", "pillar": "extreme"},
    {"topic": "Surfing Wipeouts", "pillar": "fails"},
    {"topic": "Trampoline Fails", "pillar": "fails"},
    {"topic": "Gymnast Mid-Air Recoveries", "pillar": "sports"},
    {"topic": "Cyclist Close Calls", "pillar": "near misses"},
    {"topic": "Longest Trick Shots Ever Made", "pillar": "trick shots"},
    {"topic": "Climbing Falls Caught by the Rope", "pillar": "near misses"},
    {"topic": "Funny Pet Fails", "pillar": "fails"},
    {"topic": "Cliff Jump Fails", "pillar": "fails"},
    {"topic": "Perfect Half-Court Shots", "pillar": "trick shots"},
    {"topic": "Rooftop Gap Jump Recoveries", "pillar": "parkour"},
    {"topic": "Dirt Bike Landing Saves", "pillar": "stunts"},
]

# Words that demand one specific outcome, which is what makes a topic hard to verify.
OUTCOME = re.compile(
    r"\b(?:saves?|saved|fails?|failed|falls?|misses|near[\s-]?misses?|close calls?|recover(?:y|ies|ed)|wipeouts?|crash(?:es)?|"
    r"buzzer[\s-]?beaters?|last[\s-]?second|caught|gone wrong|luckiest|impossible|bicycle kicks?|half[\s-]?court|longest|"
    r"never|first[\s-]?try|one[\s-]?take|world record)\b", re.I)
GENERAL = re.compile(r"\b(?:moments?|skills?|tricks?|stunts?|plays?|moves?|flips?|handles?|highlights?|goals?|shots?)\b", re.I)
HYPE = re.compile(r"\b(?:craziest|best|worst|insane|unbelievable|funniest|greatest|amazing|incredible|epic|wildest|"
                  r"scariest|ultimate|biggest|impressive|crazy|wild|funny|perfect)\b", re.I)


def analyze(topic: str) -> Dict[str, object]:
    """Classify a topic as broad / medium / narrow, say why, and offer a broader alternative."""
    clean = re.sub(r"^\s*(?:top|best)\s*\d+\s*", "", topic or "", flags=re.I).strip()
    outcomes = sorted({m.group(0).lower() for m in OUTCOME.finditer(clean)})
    general = bool(GENERAL.search(clean))
    if not clean:
        return {"topic": "", "breadth": "unknown", "reasons": ["Enter a topic."], "broader": None}
    if len(outcomes) >= 2 or (outcomes and not general):
        breadth = "narrow"
    elif outcomes:
        breadth = "medium"
    else:
        breadth = "broad"
    reasons = []
    if outcomes:
        reasons.append("Requires a specific visible outcome (" + ", ".join(outcomes) + "); few clips show it completely.")
    if general and not outcomes:
        reasons.append("Any impressive visible moment of the subject can qualify.")
    if not general and not outcomes:
        reasons.append("Names a subject without a required outcome.")
    return {"topic": clean, "breadth": breadth, "reasons": reasons,
            "broader": broaden(clean) if breadth != "broad" else None}


def broaden(topic: str) -> Optional[str]:
    """Drop the demanded outcome and keep the subject: 'Craziest Parkour Saves' -> 'Insane Parkour Moments'."""
    subject = OUTCOME.sub(" ", topic or "")
    subject = HYPE.sub(" ", subject)
    subject = re.sub(r"\b(?:ever|made|caught|on|camera|by|the|rope|of|to|a|in)\b", " ", subject, flags=re.I)
    subject = re.sub(r"\b(?:moments?|highlights?|plays?|moves?|skills?)\b", " ", subject, flags=re.I)
    words = [w if w.isupper() and len(w) > 1 else w.capitalize() for w in subject.split()]   # keep BMX
    if not words:   # the topic is only an outcome ("Luckiest Near Misses Caught on Camera")
        return "Unbelievable Moments On Camera"
    name = " ".join(words)
    # "Trick Shots", "Stunts", "Flips" already name a kind of moment.
    return f"Insane {name}" if GENERAL.search(name) else f"Insane {name} Moments"


def catalog(breadth: str = "all") -> List[Dict[str, str]]:
    if breadth not in ("all", "broad", "narrow"):
        raise ValueError("breadth must be all, broad or narrow")
    rows = [{**t, "breadth": "broad"} for t in BROAD] if breadth in ("all", "broad") else []
    rows += [{**t, "breadth": "narrow"} for t in NARROW] if breadth in ("all", "narrow") else []
    return rows
