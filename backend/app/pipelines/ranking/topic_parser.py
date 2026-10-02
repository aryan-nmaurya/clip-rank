import re
from typing import Tuple

class TopicParser:
    @staticmethod
    def parse_topic(raw_text: str, default_count: int = 5) -> Tuple[str, int]:
        """
        Extracts topic name and item count automatically.
        e.g., 'Top 7 Football Saves' -> ('Football Saves', 7)
        'Top 10 Craziest Basketball Moments' -> ('Craziest Basketball Moments', 10)
        'Top 5 Parkour Fails' -> ('Parkour Fails', 5)
        """
        clean = raw_text.strip()
        m = re.search(r'\b(?:top|best)\s*([0-9]{1,2})\b\s*(.*)', clean, re.IGNORECASE)
        if m:
            count = int(m.group(1))
            extracted_topic = m.group(2).strip()
            topic = extracted_topic if len(extracted_topic) > 2 else clean
            return topic, max(3, min(count, 10))

        # Default count if not specified
        return clean, default_count
