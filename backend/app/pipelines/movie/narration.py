"""Full-length, grounded commentary; natural speech timing drives rewrites."""
import json,math
from pathlib import Path
from pydantic import BaseModel,ConfigDict,Field,model_validator
from app.ai.ranking_verifier import parse_object
from app.core.runtime import run_blocking,run_process
from app.media.ffmpeg_core import FFmpegCore
from app.tts.voice_engine import TTSEngine
from app.tts.text import equivalent_spoken_text

class MovieCommentaryBeat(BaseModel):
    model_config=ConfigDict(extra='forbid')
    start:float=Field(ge=0,allow_inf_nan=False)
    end:float=Field(gt=0,allow_inf_nan=False)
    text:str=Field(min_length=12,max_length=700)

class MovieCommentaryScript(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str=Field(min_length=20,max_length=1800)
    visual_evidence:str=Field(min_length=30,max_length=1500)
    context_evidence:str=Field(min_length=20,max_length=1500)
    beats:list[MovieCommentaryBeat]=Field(default_factory=list,max_length=5)
    @model_validator(mode='after')
    def spoken_script(self):
        if not 5<=len(self.text.split())<=180 or self.text[-1] not in '.!?':
            raise ValueError('Full commentary needs complete spoken sentences and at most 180 words.')
        if any(term in self.text.lower() for term in ('intimate character detail','cinematography','the visual transition','melancholic scale','the scene captures')):
            raise ValueError('Editing notes are not spoken storytelling.')
        return self

class MovieNarrationDirector:
    @staticmethod
    async def write_beat(provider,proxy,sheet,window,previous,feedback=None):
        prompt=('CLIPRANK FULL SHORT COMMENTARY. This source excerpt is ONE time-aligned section of a longer Short. '
            'Write only narration for what happens IN THIS EXCERPT. Do not narrate later events or introduce future discoveries. '
            'Target '+str(window['target_words'])+' words, within two words of that target, for natural human speech. '
            'Write short, complete conversational sentences that add curiosity, useful details or emotional stakes. '
            'Never say "the scene captures", "the camera", "the visual transition" or other editing notes. '
            'Never invent an unseen person handing over an object: only describe a second person if that person is actually visible. '
            'Do not invent motives, relationships, outcomes or unseen action. Use conditional language for uncertain interpretations. '
            'Previous narration is context for avoiding repetition, not proof of events in this excerpt. '
            'Do not use knowledge of the film to add story details. Leave beats empty: the application owns timing. '
            'Return JSON with text, visual_evidence, context_evidence and beats:[]. '
            'End the text in a period.\n'+json.dumps({'window':window,'previous_narration':previous,
                'timing_or_quality_repair':feedback,'required_output_schema':MovieCommentaryScript.model_json_schema()}))
        error=None
        for attempt in range(2):
            repair=('\nSCHEMA REPAIR: '+str(error) if attempt else '')
            # Dense source frames make hand/object ownership and exact visible
            # actions easier to verify than a low-rate video summary alone.
            raw=(await provider.analyze_images([sheet],prompt+repair,options={'json':True}) if hasattr(provider,'analyze_images')
                else await provider.analyze_video(proxy,prompt+repair))
            try:
                value=MovieCommentaryScript.model_validate(parse_object(raw)).model_dump()
                if abs(len(value['text'].split())-window['target_words'])>2:
                    raise ValueError('Use '+str(window['target_words'])+' words, within two words of that target.')
                return value
            except (ValueError,TypeError,KeyError) as exc:error=str(exc)
        raise ValueError('Full commentary did not provide grounded spoken narration: '+str(error)[-400:])

    @staticmethod
    async def write(provider,proxy,sheet,duration,source_speech,feedback=None):
        count=max(1,math.ceil(duration/12));beats=[];visual=[];context=[]
        for idx in range(count):
            window={'start':round(idx*duration/count,3),'end':round((idx+1)*duration/count-(.3 if idx==count-1 else 0),3),
                'target_words':max(8,round((duration/count-.65)*2.9))}
            view=Path(proxy).with_name(f'commentary_window_{idx}.mp4')
            await run_blocking(run_process,['ffmpeg','-v','error','-y','-ss',str(window['start']),'-i',str(proxy),
                '-t',str(window['end']-window['start']),'-c:v','libx264','-crf','28','-preset','veryfast','-c:a','aac',str(view)])
            # A frame-only provider receives a sheet of this window, never a future payoff.
            from app.media.moments import MomentAnalyzer
            local_sheet=await run_blocking(MomentAnalyzer.contact_sheet,view,[{'start':0,'end':window['end']-window['start']}],
                view.with_suffix('.jpg'),dense=True,layout='fit')
            value=await MovieNarrationDirector.write_beat(provider,view,local_sheet,window,
                ' '.join(b['text'] for b in beats),feedback)
            beats.append({**window,'text':value['text'],'view':str(view),'sheet':str(local_sheet)})
            visual.append(value['visual_evidence']);context.append(value['context_evidence'])
        return {'text':' '.join(b['text'] for b in beats),'beats':beats,
            'visual_evidence':' '.join(visual),'context_evidence':' '.join(context)}

    @staticmethod
    async def synthesize_timed(script,output,voice,provider=None):
        """Short measured speech beats avoid Whisper's 30-second alignment limit."""
        if not script.get('beats'):return await TTSEngine.synthesize_timed(script['text'],output,voice)
        output=Path(output);parts=[];words=[];segments=[];provenance=None
        for idx,beat in enumerate(script['beats']):
            part=output.with_name(f'narration_beat_{idx}.wav')
            window=beat['end']-beat['start']
            for attempt in range(3):
                cached=json.loads(part.with_suffix('.json').read_text()) if part.is_file() and part.with_suffix('.json').is_file() else None
                speech=(cached if cached and equivalent_spoken_text(cached.get('original_text',''),beat['text']) and cached.get('voice') in (voice,voice.removeprefix('pocket:'))
                    else await TTSEngine.synthesize_timed(beat['text'],part,voice))
                # A breath is welcome; several seconds of missing narration is not.
                breath=1.5 if idx==len(script['beats'])-1 else 1.8
                if window-breath<=speech['duration']<=window-.1:break
                suggested=max(5,round(len(beat['text'].split())*(window-.55)/speech['duration']))
                problem=f'Beat {idx+1} narration lasts {speech["duration"]:.2f}s for its {window:.2f}s window. Rewrite to {suggested} words at natural speed.'
                if provider is None or attempt==2:raise ValueError(problem)
                revised=await MovieNarrationDirector.write_beat(provider,Path(beat['view']),Path(beat['sheet']),
                    {**beat,'target_words':suggested},' '.join(s['text'] for s in segments),problem)
                beat['text']=revised['text']
                script['text']=' '.join(b['text'] for b in script['beats'])
            shifted=[{**w,'start':w['start']+beat['start'],'end':w['end']+beat['start']} for w in speech['words']]
            words.extend(shifted);segments.append({'start':shifted[0]['start'],'end':shifted[-1]['end'],
                'text':speech['text'],'words':shifted})
            parts.append((part,beat,speech));provenance=speech.get('provenance',provenance)
        cmd=['ffmpeg','-v','error','-y']
        for part,_,_ in parts:cmd.extend(['-i',str(part)])
        filters=[]
        for idx,(_,beat,speech) in enumerate(parts):
            length=beat['end']-beat['start'] if idx<len(parts)-1 else speech['duration']
            filters.append(f'[{idx}:a]apad,atrim=duration={length:.4f},asetpts=PTS-STARTPTS[a{idx}]')
        filters.append(''.join(f'[a{i}]' for i in range(len(parts)))+f'concat=n={len(parts)}:v=0:a=1[voice]')
        cmd.extend(['-filter_complex',';'.join(filters),'-map','[voice]','-ar','48000','-ac','1',str(output)])
        await run_blocking(run_process,cmd)
        duration=await run_blocking(lambda:FFmpegCore.get_video_info(output)['duration'])
        result={'duration':duration,'text':' '.join(s['text'] for s in segments),'original_text':script['text'],
            'words':words,'segments':segments,'engine':'Measured full-short commentary beats','voice':voice,
            'provenance':provenance,'beats':[{'start':b['start'],'end':b['start']+s['duration'],'text':s['text']} for _,b,s in parts]}
        output.with_suffix('.json').write_text(json.dumps(result,indent=2))
        return result
