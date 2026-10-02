"""Evidence-grounded narration and two genuinely different countdown edits."""
import json
import math
import re
from app.ai.ranking_verifier import parse_object, confidence, MIN_TOPIC_CONFIDENCE
from app.pipelines.ranking.scorer import RankingScorer


def tokens(text):
    return re.findall(r"\w+", text.lower())


class ProductionDirector:
    @staticmethod
    def validate_tease(text):
        if re.search(r'\b(?:witness|observe|blunders?|looms?)\b|here we see|at number \w+ we have',text,re.I):
            raise ValueError('Use conversational language instead of a formal announcer.')
        if re.search(r'\b(?:falls?|fails?|misses?|slips?|crash(?:es)?|injur(?:y|ies|ed)|miscalculates?|los(?:e|es|ing)\b[^.?!]*\bgrip|hit(?:s|ting)?\b[^.?!]*\b(?:rail|concrete)|ends? (?:in|with)|lands? in|lacks? the distance|too (?:wide|short))\b',text,re.I):
            raise ValueError('The line reveals the outcome. Use a visible setup detail or watch-closely cue.')
        return text

    @classmethod
    async def rewrite_tease(cls,provider,moment,variant,budget,feedback,avoid=None):
        """Repair one sentence with focused evidence, rather than discard a valid edit."""
        for _ in range(3):
            prompt=('Write ONE natural spoken curiosity tease before this visible event. '
                f'Use 2–{budget} whitespace-separated words. Do not narrate the outcome. '
                'Do not use fall, fail, miss, slip, lose grip, crash, injury, witness, observe or blunder. '
                'Do not copy the event label. Invite attention to a visible detail or pose an unresolved question. '
                'Conversational tone examples: "Keep your eyes on his shoes", "Now look between those rooftops". '
                'Only use details actually supported below. Return ONLY JSON {"line":"Your short spoken tease"}.\n'+
                json.dumps({'variant':variant,'visible_action':moment['observed_action'],'visible_evidence':moment['topic_evidence'],
                    'word_budget':budget,'repair_feedback':feedback,'avoid_duplicate_line':avoid},ensure_ascii=False))
            raw=await provider.generate_text(prompt,options={'json':True})
            try:
                line=cls.validate_tease(cls.validate_line(parse_object(raw)['line'],budget))
                if avoid and tokens(line)==tokens(avoid):raise ValueError('Use a different line for this variant')
                return line
            except (ValueError,TypeError,KeyError) as exc:feedback=str(exc)
        raise ValueError(f'Could not rewrite a grounded, spoiler-free tease: {feedback}')

    @staticmethod
    def topical_roots(topic):
        generic={'funny','best','worst','epic','amazing','incredible','moments','fails','fail','saves','save','videos','clips','the','top','most'}
        return {w.rstrip('s') for w in tokens(topic) if len(w)>2 and w not in generic}

    @staticmethod
    async def rank_pool(provider,name,pool,sheets,topic):
        prompt=('COMPARATIVE RANKING. Compare all these independently topic-verified events together. Each image corresponds '
            'to the candidate at the same index below. Rank entertainment payoff, surprise, clarity and escalation relative to '
            'the other candidates. Do not equate injury severity with entertainment. The best event should earn #1. '
            'Source titles and popularity are not evidence. Give distinct relative scores, not identical 95/100 ratings. '
            'Explain why each visible payoff ranks above/below the others. Never change the source cuts. Return ONLY JSON '
            '{"rankings":[{"id":0,"score":80,"reason":"Specific comparative visible evidence"}]} with every ID exactly once.\n'+
            json.dumps({'topic':topic,'candidates':[{'id':i,'label':m['label'],'observed_action':m['observed_action'],
                'topic_evidence':m['topic_evidence']} for i,m in enumerate(pool)]},ensure_ascii=False))
        feedback=None
        for _ in range(3):
            raw=await provider.analyze_images(sheets,prompt+'\nRepair feedback: '+str(feedback),options={'json':True})
            try:
                decisions=parse_object(raw)['rankings']
                if not isinstance(decisions,list) or len(decisions)!=len(pool): raise ValueError('Every candidate needs a comparative decision')
                by_id={};scores=set()
                for item in decisions:
                    idx,score=item['id'],item['score']
                    if type(idx) is not int or idx in by_id or not 0<=idx<len(pool):raise ValueError('Invalid candidate ID')
                    if type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=100 or score in scores:raise ValueError('Relative scores must be finite and distinct')
                    if not isinstance(item.get('reason'),str) or len(item['reason'].strip())<25:raise ValueError('Specific comparative evidence required')
                    by_id[idx]=item;scores.add(score)
                return [{**m,'candidate_score':m['score'],'score':by_id[i]['score'],'ranking_reason':by_id[i]['reason'],
                         'ranking_method':f'{name} comparative payoff review'} for i,m in enumerate(pool)]
            except (ValueError,TypeError,KeyError) as exc:feedback=str(exc)
        raise ValueError(f'{name} could not establish an evidence-based comparative ranking: {feedback}')

    @staticmethod
    def cuts(pool, count, variant):
        best = sorted(pool, key=lambda m: m['score'], reverse=True)
        if variant == 'B' and len(best) > count:
            # Preserve the best payoff but use other independently verified sources.
            alternatives = best[count:count * 2 - 1]
            selected = [best[0], *alternatives, *best[1:count]]
            seen, unique = set(), []
            for item in selected:
                if item['source_id'] not in seen:
                    seen.add(item['source_id']); unique.append(item)
                if len(unique) == count:
                    break
        else:
            unique = best[:count]
        ordered = RankingScorer.score_and_order(unique, count)
        result = []
        for item in ordered:
            start, end = item['start'], item['end']
            if variant == 'A':
                start = max(start, item['event_start'] - .7)
                end = min(end, item['event_end'] + .45)
                # Do not rush speech or split the actual event to hit a fixed duration.
                if end - start < 3.4:
                    start = max(item['start'], end - 3.4)
                    end = min(item['end'], max(end, start + 3.4))
            else:
                start=max(start,item['event_start']-1.8)
                end=min(end,item['event_end']+.7)
            result.append({**item, 'start': round(start, 3), 'end': round(end, 3),
                           'verified_start': item['start'], 'verified_end': item['end']})
        return result

    @classmethod
    async def plan(cls, provider, name, pool, count, topic, language='en', feedback=None):
        variants = {key: cls.cuts(pool, count, key) for key in ('A', 'B')}
        evidence = {key: [{**{k: m[k] for k in ('source_id','assigned_rank','start','end','event_start',
                      'payoff_time','event_end','label','observed_action','topic_evidence','score')},
                      'max_narration_words':5 if idx==0 else min(10,max(3,math.floor((m['end']-m['start']-.4)*1.8)))}
                    for idx,m in enumerate(values)]
                    for key, values in variants.items()}
        prompt = (
            'You are ClipRank\'s final production director. Write two finished, distinct ranking narratives '
            'using ONLY the verified visible events below. A is fast entertainment; B is suspense and storytelling. '
            'No greetings, introductions, requests to like/subscribe, view/revenue promises, invented injuries, '
            'emotions or invisible actions. Never reveal a fall/miss/outcome before its visible payoff. '
            'Use specific visible setup details to invite attention, natural varied curiosity bridges and escalation. '
            'Talk like a friend showing an amazing clip, not a formal announcer. Never use witness, observe, '
            'blunders, looms, or "here we see". Prefer varied conversational watch-closely teases. '
            'Examples of tone (only use when grounded): "Keep your eyes on his shoes", "That landing spot looks optimistic", '
            '"Now look between those rooftops". Avoid describing the entire attempted action like a sports catalog. '
            'Do not repeat "At number...". Rank graphics already show the number. The first spoken line IS the hook: '
            'make the topic immediately clear and give an honest reason to see #1; at most 5 words. '
            'For the first entry narration must equal the hook, so it starts immediately and lasts roughly 1–2 seconds. '
            'For every later entry write a concise, grounded tease/bridge within its EXACT max_narration_words '
            'budget shown below. Count each whitespace-separated word. If the budget is 5, use no more than 5. '
            'Do not narrate an outcome prematurely. Use conversational natural English '
            'unless another language is explicitly requested. A and B must use different hooks and different spoken '
            'lines for every shared source. Keep the supplied rank/source sequence unchanged. '
            'Every second should add context, anticipation or payoff. No outro padding. Return ONLY JSON: '
            '{"variants":[{"name":"A","hook":"...","entries":[{"source_id":"...",'
            '"rank":5,"narration":"...","reason":"How this adds original context without spoiling"}]}]}. '
            'Include both A and B, all entries exactly once.\n' + json.dumps({'topic': topic, 'language': language,
                'verified_cuts': evidence, 'repair_feedback': feedback}, ensure_ascii=False))
        raw = await provider.generate_text(prompt, options={'json': True})
        try:
            response = parse_object(raw)
            provider.last_production_response = response
            if len(response['variants']) != 2:
                raise ValueError('Two variants required')
            plans = {}
            for plan in response['variants']:
                key = plan['name']
                if key not in variants or key in plans:
                    raise ValueError('Invalid variant')
                hook = cls.validate_line(plan['hook'], 5)
                roots=cls.topical_roots(topic)
                if roots and not roots.intersection(w.rstrip('s') for w in tokens(hook)):
                    raise ValueError(f'Short {key} hook must explicitly name the requested subject: {topic}')
                if len(plan['entries']) != count:
                    raise ValueError('Incomplete countdown')
                entries = []
                for idx, (script, moment) in enumerate(zip(plan['entries'], variants[key])):
                    if script['source_id'] != moment['source_id'] or type(script['rank']) is not int or script['rank'] != moment['assigned_rank']:
                        raise ValueError('Incorrect ranking order')
                    budget = min(10, max(3, math.floor((moment['end'] - moment['start'] - .4) * 1.8)))
                    try:
                        text = hook if idx==0 else cls.validate_line(script['narration'],budget)
                        if idx:cls.validate_tease(text)
                    except ValueError as exc:
                        avoid=next((m['narration_text'] for m in plans.get('A',{}).get('moments',[]) if m['source_id']==moment['source_id']),None)
                        text=await cls.rewrite_tease(provider,moment,key,budget,str(exc),avoid)
                    entries.append({**moment, 'narration_text': text, 'script_reason': str(script.get('reason',''))[:500]})
                plans[key] = {'name': key, 'hook': hook, 'moments': entries,
                              'intent': 'Fast entertainment' if key == 'A' else 'Suspense and storytelling'}
            if tokens(plans['A']['hook']) == tokens(plans['B']['hook']):
                raise ValueError('Duplicate hooks')
            by_source = {m['source_id']: tokens(m['narration_text']) for m in plans['A']['moments']}
            if any(by_source.get(m['source_id']) == tokens(m['narration_text']) for m in plans['B']['moments']):
                raise ValueError('Duplicate narration')
            return plans
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f'{name} did not produce two valid, distinct production scripts: {exc}') from exc

    @staticmethod
    def validate_line(value, max_words):
        if not isinstance(value, str) or not 2 <= len(value.split()) <= max_words:
            raise ValueError('Narration is too long or empty')
        if re.search(r'hello guys|welcome back|today we|in this video|like and subscribe|million views|guaranteed|your short spoken tease|lorem ipsum|placeholder', value, re.I):
            raise ValueError('Unwanted introduction, CTA or guarantee')
        return value.strip()

    @staticmethod
    async def final_review(provider, name, video_path, sheets, topic, timeline, feedback=None, mode='ranking'):
        prompt = (
            'FINAL RENDER QUALITY REVIEW. Inspect the actual rendered video/images, not just the planned edit. '
            'When video is supplied LISTEN to the finished audio and check its narration against the visible events. '
            'When images are supplied do not claim to have heard audio; objective audio and word-timing checks run separately. '
            'Check beginning, all ranks, transitions, captions, visible action, #1 payoff and ending. '
            'Require the whole requested topic (subject/action/outcome), complete payoffs, correct sequence N to 1, '
            'deserving escalation, truthful hook, narration grounded in the footage with no premature spoilers, '
            'readable safe captions/rank graphics, no obscured action, stretching, bad crops or unexpected blank frames. '
            'Reject low resolution/confusing footage, repetitive dead time, unfinished audio or missing captions. '
            'Reject gratuitous blood/injury close-ups and commentary that announces the specific outcome before its payoff. '
            'Do not guess. Only pass when all checks are true and confidence >= .85. '
            'Provide one concrete observation per timeline entry: its rank (null for standalone), '
            'absolute finished-video time in seconds and a specific visible event description. '
            'Describe the actual footage in the reason, not generic approval language. '
            'Return ONLY JSON: {"topic_matches":true,"hook_honest":true,"payoffs_complete":true,'
            '"rank_order_correct":true,"escalation_valid":true,"narration_grounded":true,"captions_readable":true,'
            '"framing_safe":true,"production_finished":true,"no_graphic_injury":true,"commentary_preserves_payoff":true,"confidence":.95,"reason":"Describe actual observations",'
            '"observations":[{"rank":5,"time":1.2,"visible_event":"Describe the specific visible action"}],'
            '"issues":[],"repair":"Specific feasible correction if rejected"}.\n' + json.dumps({
            'topic': topic, 'timeline': timeline, 'previous_feedback': feedback, 'mode':mode}, ensure_ascii=False))
        if mode == 'viral':
            prompt += '\nThis is a standalone viral highlight, not a countdown. Rank order/escalation checks mean coherent story and strongest payoff placement; do not demand ranking graphics. Original intelligible dialogue counts as finished narration. No invented narrator is required over actual dialogue.'
        if hasattr(provider, 'analyze_video'):
            raw = await provider.analyze_video(video_path, prompt)
            method = f'{name} final video and audio review'
        else:
            raw = await provider.analyze_images(sheets, prompt, options={'json': True})
            method = f'{name} sampled final frames + objective audio/word timing QC'
        try:
            review = parse_object(raw)
            required = ('topic_matches','hook_honest','payoffs_complete','rank_order_correct','escalation_valid',
                        'narration_grounded','captions_readable','framing_safe','production_finished','no_graphic_injury','commentary_preserves_payoff')
            passed = all(review.get(k) is True for k in required) and confidence(review['confidence']) >= MIN_TOPIC_CONFIDENCE
            if not isinstance(review.get('issues'), list) or review['issues']:
                passed = False
            if not isinstance(review.get('reason'), str) or len(review['reason'].strip())<40 or review['reason'].strip() in ('Observed evidence','Describe actual observations'):
                passed = False
            observations=review.get('observations')
            if not isinstance(observations,list) or len(observations)!=len(timeline):
                passed=False
            else:
                for observed,item in zip(observations,timeline):
                    time=observed.get('time')
                    if (observed.get('rank')!=item.get('rank') or type(time) not in (int,float) or not math.isfinite(time)
                        or not item['timeline_start']<=time<=item['timeline_start']+item['duration']
                        or not isinstance(observed.get('visible_event'),str) or len(observed['visible_event'].strip())<20
                        or observed['visible_event']=='Describe the specific visible action'):
                        passed=False
            return {**review, 'passed': passed, 'method': method}
        except (ValueError, TypeError, KeyError):
            return {'passed': False, 'method': method, 'reason': 'Final multimodal review did not provide valid acceptance evidence.'}

    @staticmethod
    async def plan_viral(provider,name,moment,sheet,transcript,feedback=None,force_commentary=False,contextual=False):
        prompt = ('STANDALONE STORY PRODUCTION. Inspect these chronological source frames and actual word-timed speech. '
            'Select a complete standalone story with hook, essential context, tension and visible payoff. No ranking mechanics. '
            'Ignore source titles. Use only visible action and supplied speech. Do not invent motives, injuries or dialogue. '
            'Write a truthful 2–6-word title, attention hook of at most 7 words, and one short grounded narration of '
            'at most 12 words for a silent/no-dialogue source only. If actual speech exists preserve it instead of competing '
            'with it. Remove filler/long dead air only when it preserves meaning and visible payoff. '
            'Return ordered non-overlapping source cuts inside the supplied window; each >=.6 seconds, max 8 cuts. '
            'Do not cut midword. Prefer a continuous cut for action. A dialogue edit may trim long pauses but retain '
            'conversational rhythm. Return ONLY JSON: {"complete_story":true,"confidence":.95,"title":"...",'
            '"hook":"...","narration":"...","observed_action":"...","cuts":[{"start":0,"end":8}]}.\n'+
            json.dumps({'window':{'start':moment['start'],'end':moment['end']},'actual_speech':transcript,
                        'repair_feedback':feedback},ensure_ascii=False))
        if force_commentary:
            prompt+='\nORIGINAL COMMENTARY REQUIRED even when source dialogue exists. Write one grounded spoken observation of at most 12 words, adding curiosity without spoiling the visible payoff. Preserve useful source sounds after narration. '+('Explain a visible detail that helps understand the outcome; no invented context.' if contextual else '')
        raw = await provider.analyze_images([sheet],prompt,options={'json':True})
        try:
            plan = parse_object(raw)
            if plan.get('complete_story') is not True or confidence(plan['confidence']) < MIN_TOPIC_CONFIDENCE:
                raise ValueError('Story/payoff is not verified')
            plan['title'] = ProductionDirector.validate_line(plan['title'],6)
            plan['hook'] = ProductionDirector.validate_line(plan['hook'],7)
            if not transcript or force_commentary:
                plan['narration'] = ProductionDirector.validate_line(plan['narration'],12)
            if not isinstance(plan.get('observed_action'),str) or not plan['observed_action'].strip():
                raise ValueError('Missing visible story evidence')
            if not isinstance(plan['cuts'],list) or not 1<=len(plan['cuts'])<=8:
                raise ValueError('Invalid story cuts')
            if (not transcript or force_commentary) and len(plan['cuts']) != 1:
                raise ValueError('A no-dialogue action story must use one complete continuous cut; do not repeat its narration.')
            previous=moment['start']
            word_edges=[(w['start'],w['end']) for s in transcript for w in s.get('words',[])]
            for cut in plan['cuts']:
                start,end=cut['start'],cut['end']
                if not all(type(t) in (int,float) and math.isfinite(t) for t in (start,end)) or not previous<=start<end<=moment['end'] or end-start<.6:
                    raise ValueError('Invalid source boundaries')
                if any(lo+.04<t<hi-.04 for lo,hi in word_edges for t in (start,end)):
                    raise ValueError('A cut interrupts source speech midword')
                previous=end
            return plan
        except (ValueError,TypeError,KeyError) as exc:
            raise ValueError(f'{name} did not produce a verified standalone edit: {exc}') from exc
