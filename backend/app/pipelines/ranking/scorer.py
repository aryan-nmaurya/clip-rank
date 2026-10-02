from typing import List, Dict, Any


class Deduplicator:
    @staticmethod
    def deduplicate_moments(moments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        unique = []
        for moment in moments:
            key = moment.get("source_id") or moment.get("url") or moment["file_path"]
            if key not in seen:
                seen.add(key)
                unique.append(moment)
        return unique


class RankingScorer:
    @staticmethod
    def score_and_order(moments: List[Dict[str, Any]], count: int):
        top = sorted(moments, key=lambda x: x.get("score", 0), reverse=True)[:count]
        if len(top) < count:
            raise ValueError(f"Top {count} requires {count} distinct source clips; found {len(top)}.")
        # The BEST source is #1; playback runs worst to best (#N -> #1).
        ranked = [{**moment, "assigned_rank": i + 1} for i, moment in enumerate(top)]
        return list(reversed(ranked))
