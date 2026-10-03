"""Format-specific checks supplement the shared encoded-video QC."""
import json,math
from app.ai.ranking_verifier import parse_object
from app.media.production_qc import ProductionQC
from app.transcription.transcriber import Transcriber
from app.core.runtime import run_process
from app.pipelines.movie.models import MovieFinalReview,MovieObservedVideo

class MovieQualityControl:
    @staticmethod
    def source_speech(video,start,end,audio_path):
        """Recheck short movie excerpts without VAD discarding a quiet actor line."""
        audio_path.parent.mkdir(parents=True,exist_ok=True)
        run_process(['ffmpeg','-v','error','-y','-ss',str(start),'-i',str(video),'-t',str(end-start),
            '-vn','-ar','16000','-ac','1',str(audio_path)])
        transcript=Transcriber.transcribe(audio_path,vad_filter=False,language='en',condition_on_previous_text=False)
        if not transcript.get('available'):raise ValueError('Source dialogue cannot be verified for accurate captions.')
        for segment in transcript['segments']:
            segment['start']+=start;segment['end']+=start
            for word in segment['words']:word['start']+=start;word['end']+=start
        return transcript

    @staticmethod
    def captions(transcript,start,end):
        captions=[]
        for segment in transcript.get('segments',[]):
            words=Transcriber.words_in_window(segment.get('words',[]),start,end)
            if words:
                ProductionQC.validate_words(words,end-start)
                captions.append({'start':words[0]['start'],'end':words[-1]['end'],
                    'text':' '.join(w['word'] for w in words),'words':words})
        return captions
    @staticmethod
    def safe_bounds(analysis,transcript):
        # Extend a model-selected boundary to the actual word/sentence boundary,
        # within its already reviewed candidate; never snip an actor's word.
        start,end=analysis['start'],analysis['end']
        for segment in transcript.get('segments',[]):
            for word in segment.get('words',[]):
                if word['start']+.025<start<word['end']-.025:start=word['start']
                if word['start']+.025<end<word['end']-.025:end=word['end']
        return start,end
    @staticmethod
    def narration_slot(regions,duration,voice_duration,payoff=None):
        cursor=0.
        # Full-short narration spans the story; explicit tease slots stop before payoff.
        limit=duration-.15 if payoff is None else min(duration-.15,max(0,payoff-.15))
        for region in sorted(regions,key=lambda r:r['start']):
            if cursor+voice_duration<=min(limit,region['start']-.15):return cursor
            cursor=max(cursor,region['end']+.15)
        if cursor+voice_duration<=limit:return cursor
        raise ValueError('There is no safe narration slot around protected dialogue. Preserve the original dialogue instead.')
    @staticmethod
    def validate_format(format,timeline,music):
        beat=timeline[0];speech=beat.get('speech');duration=beat['duration']
        if format=='DIALOGUE':
            if speech or not beat['original_speech']:raise ValueError('Dialogue requires actor speech/captions and no TTS.')
        elif format=='COMMENTARY':
            if not speech:raise ValueError('Commentary audio is missing.')
            offset=beat['narration_offset'];end=offset+speech['duration']
            if end>duration-.1:raise ValueError('Narration is truncated by the ending.')
            if beat.get('commentary_scope')=='full_short':
                if speech['duration']<duration*.75 or end<duration-2 or offset>1:
                    raise ValueError('Full-short commentary must continue through the story and ending, not stop after an opening line.')
            elif end>=beat['payoff_relative']:raise ValueError('Narration intrudes on the payoff.')
            if any(offset<r['end']+.1 and end>r['start']-.1 for r in beat['protected_dialogue']):
                raise ValueError('Narration overlaps protected original dialogue.')
            ProductionQC.validate_words(speech['words'],speech['duration'],speech['text'])
        elif format=='AESTHETIC':
            if speech or not music or not music.get('rights_passed'):raise ValueError('Cinematic edits require authorized audio and no narration.')
        else:raise ValueError('Unknown movie format.')
        for segment in beat['original_speech']:
            ProductionQC.validate_words(segment['words'],duration)
        return True
    @staticmethod
    async def observe(provider,name,qc,duration):
        # No proposed plot/title is shown here: it can anchor a reviewer into
        # confirming an invented action that never appears in the video.
        prompt=('CLIPRANK INDEPENDENT MOVIE EVIDENCE. Watch the ACTUAL finished video before considering any proposed story. '
            'No story summary or expected plot is provided. Describe only visible actions and audible words. '
            'Keep people, animals and their actions distinct. Do not infer riding, fighting, rescue, death, motives or relationships '
            'unless actually shown. Do not use prior knowledge of a film to fill missing events. '
            'Transcribe audible narrator words and original actor words separately; use empty strings if absent. '
            'Written headline text is not spoken narration: never transcribe it as audible words. '
            'For still-frame review do not claim to hear audio: leave both transcript fields empty. '
            'Return JSON conforming to this schema. Observation time is numeric seconds from clip start, never "00:13". '
            'Confidence is 0–1, never a percentage. Each observation must contain at least 20 characters. '
            +json.dumps({'duration':duration,'required_output_schema':MovieObservedVideo.model_json_schema()}))
        error=None
        for attempt in range(2):
            repair=('\nSCHEMA REPAIR: '+str(error) if attempt else '')
            raw=(await provider.analyze_video(qc['proxy'],prompt+repair) if hasattr(provider,'analyze_video')
                else await provider.analyze_images(qc['sheets'],prompt+repair,options={'json':True}))
            try:
                value=MovieObservedVideo.model_validate(parse_object(raw)).model_dump()
                if value['confidence']<.85 or any(o['time']>duration for o in value['observations']):
                    raise ValueError('Independent video evidence is uncertain or outside the clip.')
                return value
            except (ValueError,TypeError,KeyError) as exc:error=str(exc)
        raise ValueError('Independent movie review did not provide valid evidence: '+str(error)[-400:])
    @staticmethod
    async def review(provider,name,qc,analysis,format,timeline,music,feedback=None):
        from app.core import qc as quality_control
        if not quality_control.enabled():
            return {'passed':True,'skipped':True,'method':'quality control off','reason':'Quality control is off: the finished clip was not reviewed.','observations':[]}
        evidence=await MovieQualityControl.observe(provider,name,qc,timeline[0]['duration'])
        prompt=('CLIPRANK FINAL MOVIE SHORT REVIEW. Inspect the ACTUAL finished MP4 or sampled final frames. '
            'Video: listen to finished audio; images: do not claim you heard it. Objective audio/caption checks run separately. '
            'DIALOGUE: actors carry the scene; no TTS, strong line intact, accurate captions. COMMENTARY: concise original '
            'narration adds grounded context/anticipation, no invented plot facts or outcome spoilers, never interrupts actor dialogue. '
            'Narration must sound like complete, natural spoken sentences, not an editing note, academic description of '
            'camera transitions or visual scale. If explanation adds no story value, reject the commentary format. '
            'When commentary_scope is full_short, narration must sustain the whole story rather than stop after the hook. '
            'Separate written headline text from spoken audio; do not transcribe a headline as if it were narration. '
            'AESTHETIC: imagery and its licensed music/source soundtrack carry the moment, no unnecessary narration/text. '
            'No ranks required. Check first second/hook, middle, speech, visible payoff and ending, mobile caption readability, '
            'faces/action visibility, no broken crop/stretched frames/black/frozen sections, intentional pacing and finished audio. '
            'Do not demand captions when no speech exists. Reject filler, clipped captions or distracting credit/header positioning. '
            'Return JSON with all of these boolean checks: scene_matches,hook_honest,payoff_complete,framing_safe,captions_readable,'
            'audio_finished,format_correct,dialogue_preserved,commentary_grounded,no_spoilers,production_finished; '
            'also confidence 0–1, reason (specific observations), observations [{time,visible_event}], issues [], repair (specific correction). '
            'Observation time MUST be a NUMBER of seconds from the finished clip start (13.2), never a string like "00:13". '
            'Each visible_event must contain at least 20 characters. Confidence is a fraction (0.95), never 95. '
            'Compare proposed title/scene claims against the independent evidence. If they contradict visible actions, '
            'scene_matches MUST be false even when the MP4 itself is technically sound. '
            'Only pass when every check is true and confidence >= .85.\n'+json.dumps({'independent_evidence':evidence,'scene':analysis,'format':format,
                'timeline':timeline,'music':music,'repair_feedback':feedback,
                'required_output_schema':MovieFinalReview.model_json_schema()},ensure_ascii=False))
        error=None
        for attempt in range(2):
            repair=('\nSCHEMA REPAIR: '+str(error)+'. Return numeric seconds, fraction confidence and every field with its required type.' if attempt else '')
            if hasattr(provider,'analyze_video'):
                raw=await provider.analyze_video(qc['proxy'],prompt+repair);method=f'{name} finished movie video/audio review'
            else:
                raw=await provider.analyze_images(qc['sheets'],prompt+repair,options={'json':True});method=f'{name} finished frames + measured audio/caption QC'
            try:
                value=MovieFinalReview.model_validate(parse_object(raw)).model_dump()
                if any(o['time']>timeline[0]['duration'] for o in value['observations']):
                    raise ValueError('Review observations escape the finished clip duration.')
            except (ValueError,TypeError,KeyError) as exc:
                error=str(exc);continue
            checks=('scene_matches','hook_honest','payoff_complete','framing_safe','captions_readable','audio_finished',
                'format_correct','dialogue_preserved','commentary_grounded','no_spoilers','production_finished')
            passed=all(value.get(k) is True for k in checks)
            passed=passed and value['confidence']>=.85 and value['issues']==[]
            return {**value,'passed':bool(passed),'method':method,'independent_evidence':evidence}
        return {'passed':False,'method':method,'reason':'Final movie review did not provide valid acceptance evidence.',
            'schema_error':str(error)[-600:]}
