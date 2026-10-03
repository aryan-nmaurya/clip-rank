"""Ranking needs positive semantic evidence; motion or source titles cannot prove a topic."""
import json
import math
import re
from app.media.captions import clean_label

MIN_TOPIC_CONFIDENCE = .85


def parse_object(raw):
    text = (raw or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("Expected an object")
    return result


def confidence(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Confidence must be an explicit finite number between 0 and 1")
    return float(value)


class RankingVerifier:
    @staticmethod
    async def evaluate(provider, name, moments, image_path, topic, repair_feedback=None):
        prompt = (
            "Evaluate the video frames in temporal order. Each ROW is one candidate cut with an ID and time range. "
            "The requested topic is a strict acceptance condition. Every cut must visibly show the requested subject, "
            "action AND outcome. For 'parkour fails', a person running/jumping or a successful stunt is NOT a match: "
            "the visible parkour attempt must fail, slip, fall or miss its landing within this cut. For 'football saves', "
            "playing football is insufficient: the save must be visible. Reject wrong animals/sports/actions, "
            "intro screens, preparation-only cuts, unrelated reactions, obscured action or uncertain evidence. "
            "Reject gratuitous blood or graphic injury close-ups: set graphic_injury=true whenever present. "
            "Ignore embedded captions, hashtags and source titles as proof. Describe only what the FRAMES show. "
            "Never accept a ranking/countdown/compilation. Return an entry for each ID. Only set matches_topic=true "
            "and complete_action=true when the requested event and outcome are visible. "
            "Give a factual 2–6 word label and a complete conversational commentary sentence (at most 12 words), "
            "both specific to the visible event. Do not reuse an uploader's title. "
            "Return ONLY JSON with actual booleans and numeric confidence: "
            '{"moments":[{"id":0,"matches_topic":true,"complete_action":true,"already_ranked":false,"graphic_injury":false,'
            '"topic_relevance":0.95,"confidence":0.95,"observed_action":"What visibly happens",'
            '"topic_evidence":"Specific visible evidence of the requested subject/action/outcome",'
            '"label":"Observed action label","commentary":"A factual sentence about this exact cut.",'
            '"event_start":1.0,"payoff_time":3.5,"event_end":5.5,'
            '"score":80,"reason":"Specific observed payoff"}]} '
            'Event timestamps must be absolute SOURCE seconds inside the proposed start/end range; '
            'event_start begins the complete attempt, payoff_time is the actual visible failure/outcome, '
            'event_end includes the landing/reaction needed to understand it. Use the printed frame times.\n'
            + json.dumps({"requested_topic": topic, 'independent_review_feedback':repair_feedback,"candidates": [
                {"id": i, "start": m["start"], "end": m["end"]} for i, m in enumerate(moments)]}, ensure_ascii=False)
        )
        raw = await provider.analyze_images([image_path], prompt)
        if not raw:
            raise ValueError(f"{name.title()} could not inspect the footage for topic '{topic}'. Check your vision connection in Settings. Ranking cannot use unverified footage.")
        try:
            response = parse_object(raw)
            if not isinstance(response.get("moments"), list):
                raise ValueError("Missing moment decisions")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{name.title()} returned no usable topic verification. Check the image-capable model and retry.") from exc
        verified = []
        seen = set()
        for item in response["moments"]:
            try:
                idx = item["id"]
                if type(idx) is not int or idx in seen or not 0 <= idx < len(moments):
                    continue
                seen.add(idx)
                if item.get("matches_topic") is not True or item.get("complete_action") is not True or item.get("already_ranked") is not False or item.get('graphic_injury') is not False:
                    continue
                relevance, certainty = confidence(item["topic_relevance"]), confidence(item["confidence"])
                if min(relevance, certainty) < MIN_TOPIC_CONFIDENCE:
                    continue
                for key in ("label", "commentary", "observed_action", "topic_evidence", "reason"):
                    if not isinstance(item.get(key), str) or not item[key].strip():
                        raise ValueError("Missing observed description")
                if len(item["commentary"].split()) > 12 or not 2 <= len(item["label"].split()) <= 6:
                    continue
                score = item["score"]
                if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
                    continue
                anchor = [item[k] for k in ('event_start','payoff_time','event_end')]
                if any(type(t) not in (int, float) or not math.isfinite(t) for t in anchor):
                    continue
                if not moments[idx]['start'] <= anchor[0] < anchor[1] <= anchor[2] <= moments[idx]['end']:
                    continue
                verified.append({**moments[idx], "score": round(score), "label": clean_label(item["label"], 6),
                    'event_start': anchor[0], 'payoff_time': anchor[1], 'event_end': anchor[2],
                    "commentary": item["commentary"].strip(), "observed_action": item["observed_action"][:500],
                    "topic_evidence": item["topic_evidence"][:700], "reason": item["reason"][:600],
                    "topic_relevance": relevance, "topic_confidence": certainty, "topic_verified": True,
                    "verified_topic": topic, "already_ranked": False, 'graphic_injury':False,"analysis_basis": f"{name} verified topic and action"})
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(verified, key=lambda m: m["score"], reverse=True)

    @staticmethod
    async def review(provider, name, moment, image_path, topic, video_path=None):
        # Describe the footage before revealing the requested topic/labels.
        # Otherwise a model can affirm "fail" even for a successful rail run.
        blind_prompt=(
            'BLIND SOURCE EVENT REVIEW. Describe only this chronological footage. No intended title or topic is supplied. '
            'Distinguish intentional rolls, controlled drops, safe landings and planned cartwheels from actual failures. '
            'Only call an outcome failed when an unintended slip, missed landing, uncontrolled fall or collision is clearly visible. '
            'If success/failure is uncertain, say uncertain. Watch the ending: the visible event must finish with its outcome and aftermath. '
            'Do not infer a failure from risky preparation or camera shake. Return ONLY JSON '
            '{"observed_action":"Specific action and outcome actually visible", "outcome":"successful|failed|uncertain",'
            '"complete_action":true,"confidence":0.95,"reason":"Specific visible evidence"}.')
        if video_path is not None and hasattr(provider,'analyze_video'):
            blind_raw=await provider.analyze_video(video_path,blind_prompt)
        else:blind_raw=await provider.analyze_images([image_path],blind_prompt)
        observed=None
        try:
            observed=parse_object(blind_raw)
            if (observed.get('complete_action') is not True or confidence(observed['confidence'])<MIN_TOPIC_CONFIDENCE
                or observed.get('outcome') not in ('successful','failed')
                or not isinstance(observed.get('observed_action'),str) or len(observed['observed_action'].strip())<20):
                raise ValueError('The full event/outcome is not independently visible.')
            if re.search(r'\bfails?\b',topic,re.I) and observed['outcome']!='failed':
                raise ValueError('Independent observation shows a successful event, not a visible failure.')
        except (ValueError,TypeError,KeyError) as exc:
            return {'passed':False,'method':f'{name} blind source review','reason':str(exc),
                'independent_observation':observed}
        prompt = (
            "Independently check this exact proposed cut, shown in chronological frames with its final footage framing. "
            "Verify ALL requested subjects, actions and outcomes are visibly present. The whole topic must match; "
            "a parkour attempt without visible failure cannot be called a parkour fail. Source captions are not evidence. "
            "Check that the proposed label and commentary each describe ONLY visible action in THIS cut. "
            "Reject uncertain cases and cuts missing the payoff, or cropping the subject/action out. "
            "Watch through the END: the full landing, impact, recovery or reaction must finish before the cut. "
            "An attempt still in progress in the final frames is incomplete even if failure looks likely. "
            "Return ONLY JSON with actual booleans: "
            '{"matches_topic":true,"complete_action":true,"label_matches":true,"commentary_matches":true,'
            '"confidence":0.95,"topic_evidence":"Observed proof","reason":"Observed reason"}.\n'
            + json.dumps({"requested_topic": topic, "label": moment["label"], "commentary": moment["commentary"],
                'independent_observation':observed}, ensure_ascii=False)
        )
        if video_path is not None and hasattr(provider,'analyze_video'):
            raw=await provider.analyze_video(video_path,prompt)
        else:raw = await provider.analyze_images([image_path], prompt)
        if not raw:
            raise ValueError(f"{name.title()} could not verify the final cut and its labels. Retry after connecting your vision model.")
        try:
            result = parse_object(raw)
            certainty = confidence(result["confidence"])
            passed = all(result.get(key) is True for key in ("matches_topic", "complete_action", "label_matches", "commentary_matches"))
            passed = passed and certainty >= MIN_TOPIC_CONFIDENCE and isinstance(result.get("topic_evidence"), str) and bool(result["topic_evidence"].strip())
            return {"passed": passed, "confidence": certainty, "method": f"{name} complete source-video review" if video_path else f"{name} final cut review",
                    'independent_observation':observed,
                    "topic_evidence": str(result.get("topic_evidence") or "")[:700],
                    "reason": str(result.get("reason") or "The cut or descriptions do not match the requested topic.")[:600]}
        except (ValueError, TypeError, KeyError):
            return {"passed": False, "reason": "The vision model did not provide valid final-cut verification."}
