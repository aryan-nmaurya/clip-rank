import asyncio,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from app.pipelines.movie.models import MovieMomentAnalysis,DIMENSIONS,ProtectedDialogueRegion
from app.pipelines.movie.director import MovieMomentScorer,MovieFormatDirector
from app.pipelines.movie.quality import MovieQualityControl
from app.studio.policy import RightsPolicyEngine


def analysis(format='DIALOGUE'):
    scores={k:88 for k in DIMENSIONS};scores.update(context_requirement=10,interruption_cost=20)
    if format!='DIALOGUE':scores.update(dialogue_strength=25,dialogue_importance=25)
    if format=='AESTHETIC':scores['commentary_potential']=20
    return {'start':0.,'end':6.,'payoff_timestamp':3.,'title':'The changing colors',
        'description':'Colored shapes move across the frame and reveal a brighter pattern.',
        'reason':'The moving colors build toward the visible pattern change at the center.',
        'header_text':'Watch those colors','commentary':'Look closely at the changing pattern.',
        'commentary_value':'The original framing directs attention to the revealing pattern, without announcing the change.',
        'mood':'action','complete_moment':True,'clean_source':True,'contains_watermark':False,'confidence':.95,'scores':scores}


def test_format_decisions_preserve_dialogue_and_do_not_force_tts():
    a=analysis();assert MovieFormatDirector.choose(a,True,True,True)['format']=='DIALOGUE'
    a=analysis('COMMENTARY');assert MovieFormatDirector.choose(a,False,True,True)['format']=='COMMENTARY'
    a=analysis('AESTHETIC');assert MovieFormatDirector.choose(a,False,True,True)['format']=='AESTHETIC'
    assert MovieFormatDirector.choose(a,False,False,False)['format']=='REJECT'
    assert MovieMomentScorer.qualifies(a)
    a['contains_watermark']=True;assert not MovieMomentScorer.qualifies(a)


def test_scores_and_untrusted_media_parameters_are_validated():
    a=analysis();MovieMomentAnalysis.model_validate(a)
    a['scores']['music_fit']=float('nan')
    with pytest.raises(ValidationError):MovieMomentAnalysis.model_validate(a)
    a=analysis();a['ffmpeg']='arbitrary model command'
    with pytest.raises(ValidationError):MovieMomentAnalysis.model_validate(a)
    with pytest.raises(ValidationError):ProtectedDialogueRegion(start=4,end=3)
    with pytest.raises(ValidationError):ProtectedDialogueRegion(start=float('nan'),end=3)


def test_rights_declarations_do_not_pass_documented_policy():
    asset={'source_url':'user-upload:test','source_type':'user_owned_upload','creator':'User',
        'license':'User-authorized audiovisual reuse','rights_status':'user_authorized','fetched_at':'2026-10-03',
        'authorization_attested':True}
    result=RightsPolicyEngine.evaluate([asset],'user_managed')
    assert result['passed'] and not result['rights_verified']
    assert not RightsPolicyEngine.evaluate([asset],'documented_permission')['passed']
    asset['authorization_attested']=False;assert not RightsPolicyEngine.evaluate([asset],'user_managed')['passed']
    asset.update(license='CC BY-NC 3.0',rights_status='commercial_use_permitted',license_evidence_url='https://example.com/license')
    assert not RightsPolicyEngine.evaluate([asset],'documented_permission')['passed']


def test_commentary_slot_never_interrupts_an_actor_or_payoff():
    assert MovieQualityControl.narration_slot([{'start':3,'end':5}],8,2,6)==0
    assert MovieQualityControl.narration_slot([{'start':0,'end':2}],8,2,6)==2.15
    with pytest.raises(ValueError,match='no safe narration slot'):
        MovieQualityControl.narration_slot([{'start':0,'end':5}],8,2,6)


def test_full_short_commentary_rejects_an_opening_tease_and_reaches_the_ending():
    text='Watch the colors change.'
    def beat(length):
        words=[{'word':word,'start':i*length/4,'end':(i+1)*length/4} for i,word in enumerate(text.split())]
        return {'duration':6.,'payoff_relative':3.,'narration_offset':0.,'commentary_scope':'full_short',
            'protected_dialogue':[],'original_speech':[],
            'speech':{'duration':length,'text':text,'words':words}}
    with pytest.raises(ValueError,match='continue through the story'):
        MovieQualityControl.validate_format('COMMENTARY',[beat(1.4)],None)
    assert MovieQualityControl.validate_format('COMMENTARY',[beat(5.)],None)
    assert MovieQualityControl.narration_slot([],6.,5.)==0.


def test_commentary_beats_keep_measured_words_on_their_video_sections(tmp_path,monkeypatch):
    from app.pipelines.movie.narration import MovieNarrationDirector
    from app.tts.voice_engine import TTSEngine
    from app.core.runtime import run_process
    calls=[]
    async def synthesize(text,path,voice):
        calls.append(text)
        run_process(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=600:duration=2.6','-ar','48000',str(path)])
        words=[{'word':word,'start':i*.6,'end':(i+1)*.6} for i,word in enumerate(text.split())]
        return {'duration':2.6,'text':text,'words':words,'provenance':{'engine':'Explicit test-only fixture'}}
    monkeypatch.setattr(TTSEngine,'synthesize_timed',synthesize)
    script={'text':'Watch the moving colors. Now the pattern changes.',
        'beats':[{'start':0.,'end':3.,'text':'Watch the moving colors.'},
                 {'start':3.,'end':6.,'text':'Now the pattern changes.'}]}
    speech=asyncio.run(MovieNarrationDirector.synthesize_timed(script,tmp_path/'voice.wav','pocket:alba'))
    assert len(calls)==2 and speech['duration']==pytest.approx(5.6,.02)
    assert speech['words'][4]['start']==3. and speech['words'][-1]['end']==5.4
    assert speech['beats'][1]['start']==3. and speech['beats'][1]['end']==5.6
    timeline=[{'duration':6.,'payoff_relative':3.,'narration_offset':0.,'commentary_scope':'full_short',
        'protected_dialogue':[],'original_speech':[],'speech':speech}]
    assert MovieQualityControl.validate_format('COMMENTARY',timeline,None)


def test_mobile_crop_jumps_at_a_shot_cut_instead_of_panning_across_the_new_subject(tmp_path):
    from app.core.runtime import run_process
    from app.media.reframer import VideoReframer
    from app.media.ffmpeg_core import FFmpegCore
    from PIL import Image
    source=tmp_path/'two_subjects.mp4'
    run_process(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=black:s=640x360:r=30:d=2',
        '-vf','drawbox=x=0:y=0:w=200:h=360:color=red:t=fill,drawbox=x=440:y=0:w=200:h=360:color=blue:t=fill',
        '-c:v','libx264','-pix_fmt','yuv420p',str(source)])
    framing={'layout':'tracked','crop_width':200,'points':[{'time':0.,'x':0.},
        {'time':1.,'x':440.,'cut':True},{'time':1.9,'x':440.}]}
    result=VideoReframer.reframe_to_vertical(source,tmp_path/'vertical.mp4',duration=2.,framing=framing,width=360,height=640)
    before=Image.open(FFmpegCore.extract_frame(result,tmp_path/'before.jpg',.9)).getpixel((180,320))
    after=Image.open(FFmpegCore.extract_frame(result,tmp_path/'after.jpg',1.1)).getpixel((180,320))
    assert before[0]>180 and before[2]<50
    assert after[2]>180 and after[0]<50


def run_movie_fixture(isolated_app,long_footage,monkeypatch,format,whole=False,expect=12):
    from app.core import database,config
    from app.storage import manager
    from app.ai.router import AIRouter
    from app.media.source_screening import SourceScreening
    from app.media.ffmpeg_core import FFmpegCore
    from app.pipelines.movie.analyzer import MovieSceneAnalyzer
    from app.pipelines.movie.movie_pipeline import MoviePipeline
    from app.pipelines.movie.music import CinematicMusicDirector
    from app.pipelines.movie.narration import MovieNarrationDirector
    from app.tts.voice_engine import TTSEngine
    from app.transcription.transcriber import Transcriber
    movie=isolated_app['root']/'output'/'movie';movie.mkdir()
    monkeypatch.setattr(config,'MOVIE_OUTPUT_DIR',movie);monkeypatch.setattr(manager,'MOVIE_OUTPUT_DIR',movie)
    monkeypatch.setattr(config,'PROJECTS_STORAGE_DIR',isolated_app['projects'])
    async def provider(*args,**kw):return SimpleNamespace(analyze_video=object()),'explicit test-only fixture'
    monkeypatch.setattr(AIRouter,'get_active_provider',provider)
    monkeypatch.setattr(SourceScreening,'inspect',lambda *a:{'sheet':Path('test-only-sheet')})
    a=analysis(format);a['end']=12.;a['frame_fingerprint']='01'*72
    monkeypatch.setattr(MovieSceneAnalyzer,'proposals',lambda *args:([dict(a)],{'sampled_frames':12,'full_source_analyzed':True}))
    async def understand(*args,**kw):
        if kw.get('source_check'):return {'clean_source':True,'contains_watermark':False,'confidence':.95,'reason':'Explicit test-only clean fixture source.'}
        return dict(a)
    monkeypatch.setattr(MovieSceneAnalyzer,'understand',understand)
    monkeypatch.setattr(CinematicMusicDirector,'select',lambda _:None)
    async def full_script(*args,**kw):return {'text':a['commentary'],
        'visual_evidence':'Colored shapes visibly change their pattern during this explicit fixture.',
        'context_evidence':'Uses only visible test-fixture evidence.'}
    monkeypatch.setattr(MovieNarrationDirector,'write',full_script)
    monkeypatch.setattr(MoviePipeline,'credit_graphic',lambda *a:pytest.fail('Copyright text must not be burned onto movie clips.'))
    words=[{'word':'Stay','start':3.5,'end':3.8},{'word':'back','start':3.8,'end':4.1},{'word':'now.','start':4.1,'end':4.4}]
    monkeypatch.setattr(Transcriber,'transcribe',lambda _,**kw: {'available':True,'segments':[
        {'start':3.5,'end':4.4,'text':'Stay back now.','words':words}] if format=='DIALOGUE' else [],'text':'Stay back now.' if format=='DIALOGUE' else ''})
    voice_calls=[]
    async def voice(text,path,*args):
        voice_calls.append(text)
        from app.core.runtime import run_process
        run_process(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=1000:duration=11','-ar','48000',str(path)])
        timed=[{'word':w,'start':i*(10.8/len(text.split())),'end':(i+1)*(10.8/len(text.split()))} for i,w in enumerate(text.split())]
        return {'duration':11.,'text':text,'words':timed,'segments':[{'start':0,'end':10.8,'text':text,'words':timed}],
            'provenance':{'engine':'Explicit synthetic test fixture'}}
    monkeypatch.setattr(TTSEngine,'synthesize_timed',voice)
    async def review(*args,**kw):return {'passed':True,'reason':'Explicit test-only reviewed moving pattern output.',
        'observations':[{'time':3,'visible_event':'Colored patterns move through the visible frame.'}]}
    monkeypatch.setattr(MovieQualityControl,'review',review)
    database.create_project('movie_project','movie','Test movie',{});database.create_job('movie_job','movie_project')
    settings={'authorization_attested':True,'source_audio_authorized':True,'source_title':'Fixture',
        'source_creator':'Test fixture','source_license':'Original test fixture','license_reference':'https://example.com/fixture',
        'source_attribution':'Test fixture · Original','rights_policy':'documented_permission','whole_video':whole}
    asyncio.run(MoviePipeline.run('movie_job','movie_project',str(long_footage[0]),1,settings,lambda *args:None))
    project=database.get_project('movie_project');assert project['status']=='COMPLETED'
    assert len(project['clips'])==1 and project['clips'][0]['subtitle']=={'DIALOGUE':'Dialogue Moment','COMMENTARY':'Commentary Moment','AESTHETIC':'Cinematic Moment'}[format]
    assert len(voice_calls)==(1 if format=='COMMENTARY' else 0)
    record=project['result_data']['moments'][0]
    assert record['format']==format and record['qc']['passed'] and record['qc']['format_checked'] and record['final_review']['passed']
    path=movie/(project['clips'][0]['id']+'.mp4');FFmpegCore.validate_output(path,expect)
    assert database.clip_passed_production_qc(project['clips'][0])
    assert (movie/'movie_project_metadata.json').is_file()
    assert project['result_data']['analysis_archive']['verified_analyses']
    assert record['framing']['layout']=='tracked'
    if format=='COMMENTARY':
        assert record['timeline'][0]['commentary_scope']=='full_short'
        assert record['timeline'][0]['speech']['duration']>=record['duration']*.75
    assert not (isolated_app['temp']/'movie_job').exists()
    assert long_footage[0].is_file()  # The user's source outside job storage is untouched.
    return project,record


@pytest.mark.parametrize('format',['DIALOGUE','COMMENTARY','AESTHETIC'])
def test_finished_movie_formats_render_qc_and_clean_temp(isolated_app,long_footage,monkeypatch,format):
    run_movie_fixture(isolated_app,long_footage,monkeypatch,format,whole=False,expect=12)


def test_a_movie_source_that_fits_in_a_short_is_used_whole(isolated_app,long_footage,monkeypatch):
    # The 24 s fixture is the entire source: the finished clip is all 24 s, not the 12 s window the analyzer pointed at.
    project,record=run_movie_fixture(isolated_app,long_footage,monkeypatch,'AESTHETIC',whole=True,expect=24)
    assert project['clips'][0]['duration']>23.5 and record['duration']>23.5


def test_a_feature_length_source_still_has_scenes_picked_from_it(isolated_app,long_footage,monkeypatch):
    from app.pipelines.movie import movie_pipeline
    monkeypatch.setattr(movie_pipeline,'MAX_SHORT_SECONDS',10.0)      # pretend the 24 s source is too long to be a Short
    project,record=run_movie_fixture(isolated_app,long_footage,monkeypatch,'AESTHETIC',whole=True,expect=12)
    assert project['clips'][0]['duration']<13


def test_movie_failure_and_cancellation_clean_only_managed_workspace(isolated_app,monkeypatch):
    from app.core import database
    from app.ai.router import AIRouter
    from app.pipelines.movie.movie_pipeline import MoviePipeline
    from app.storage.manager import StorageManager
    database.create_project('reject_movie','movie','Rejected',{});database.create_job('reject_job','reject_movie')
    temp=StorageManager.get_job_temp_dir('reject_job');(temp/'audio'/'temp.wav').write_bytes(b'temporary')
    with pytest.raises(ValueError,match='Confirm ownership'):
        asyncio.run(MoviePipeline.run('reject_job','reject_movie','https://example.com/movie',1,{},lambda *a:None))
    assert not temp.exists()
    assert database.get_project('reject_movie')['status']=='FAILED'


def test_movie_api_requires_rights_and_blocks_foreign_origins(isolated_app,monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.server import app
    with TestClient(app,client=('127.0.0.1',1234)) as client:
        response=client.post('/api/projects/movie',data={'video_url':'https://example.com/movie'})
        assert response.status_code==400 and 'permission' in response.json()['detail']
        assert client.post('/api/projects/movie',headers={'Origin':'https://foreign.example'},data={}).status_code==403
        assert client.get('/api/movie/music').json()==[]

def test_complete_movie_sampling_is_bounded_and_covers_the_end(footage):
    from app.pipelines.movie.analyzer import MovieSceneAnalyzer
    candidates,metrics=MovieSceneAnalyzer.proposals(footage[0],{'segments':[]},6,1)
    assert metrics['sampled_frames']<=1800 and metrics['scene_boundaries'][-1]==6
    assert metrics['candidate_windows']>0 and len(candidates)<=metrics['deep_analysis_budget']
    assert candidates[0]['start']==0 and candidates[-1]['end']==6


def test_quiet_dialogue_gets_a_deep_analysis_slot(monkeypatch,tmp_path):
    import numpy as np
    import app.pipelines.movie.analyzer as module
    monkeypatch.setattr(module.FFmpegCore,'get_video_info',lambda _: {'duration':120.,'has_audio':False})
    frames=np.zeros((240,90,160),dtype=np.uint8)
    frames[:120]=np.random.default_rng(1).integers(0,256,frames[:120].shape,dtype=np.uint8)
    frames[120:]=100
    monkeypatch.setattr(module,'run_process',lambda *a,**kw:frames.tobytes())
    transcript={'segments':[{'start':100.,'end':118.,'words':[{'word':'spoken'}]*20}]}
    candidates,metrics=module.MovieSceneAnalyzer.proposals(tmp_path/'quiet.mp4',transcript,35,3)
    assert any(c['speech_seed'] and c['start']==99.82 and c['end']==118.35 for c in candidates)
    assert len(candidates)<=metrics['deep_analysis_budget']<=20


def test_final_movie_review_repairs_schema_and_still_rejects_quality_issues(tmp_path):
    checks=('scene_matches','hook_honest','payoff_complete','framing_safe','captions_readable','audio_finished',
        'format_correct','dialogue_preserved','commentary_grounded','no_spoilers','production_finished')
    good={**{k:True for k in checks},'confidence':.95,
        'reason':'The actor stays visible and the finished audio and captions preserve the complete exchange.',
        'observations':[{'time':2.,'visible_event':'The actor delivers the intact line in a clear close-up.'}],
        'issues':[],'repair':'None required.'}
    calls=[]
    class Provider:
        async def analyze_video(self,path,prompt):
            calls.append(prompt)
            if 'INDEPENDENT MOVIE EVIDENCE' in prompt:
                return json.dumps({'actual_scene_summary':'The actor stands in a room and delivers a short line clearly.',
                    'observations':good['observations'],'narration_text':'','original_dialogue':'Stay back now.','confidence':.95})
            return json.dumps({**good,'observations':[{'time':'00:02','visible_event':'The actor delivers the intact line in a clear close-up.'}]} if len(calls)==2 else good)
    qc={'proxy':tmp_path/'final.mp4'};timeline=[{'duration':6.}]
    result=asyncio.run(MovieQualityControl.review(Provider(),'gemini',qc,analysis(),'DIALOGUE',timeline,None))
    assert result['passed'] and len(calls)==3 and 'SCHEMA REPAIR' in calls[2]
    assert 'The changing colors' not in calls[0] and result['independent_evidence']['confidence']==.95
    good['audio_finished']=False;good['issues']=['Dialogue is inaudible.']
    result=asyncio.run(MovieQualityControl.review(Provider(),'gemini',qc,analysis(),'DIALOGUE',timeline,None))
    assert not result['passed']

def test_licensed_music_is_bound_to_audio_identity_and_mood(isolated_app,tmp_path):
    from app.core.runtime import run_process
    from app.pipelines.movie.music import CinematicMusicDirector
    source=tmp_path/'tone.wav'
    run_process(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=3',str(source)])
    with pytest.raises(ValueError,match='requires'):
        CinematicMusicDirector.register(source,'Tone','Owner','Original','permission-record',['action'])
    track=CinematicMusicDirector.register(source,'Tone','Owner','Original','permission-record',['action'],rights_attested=True)
    assert CinematicMusicDirector.select('action')['id']==track['id']
    assert CinematicMusicDirector.select('dark') is None
    path=CinematicMusicDirector.path(track);path.write_bytes(b'changed')
    assert CinematicMusicDirector.select('action') is None


def test_movie_analysis_repairs_schema_without_relaxing_validation(tmp_path):
    from app.pipelines.movie.analyzer import MovieSceneAnalyzer
    a=analysis();bad={**a,'confidence':95,'commentary_value':85}
    calls=[]
    class Provider:
        async def analyze_images(self,images,prompt,**kw):
            calls.append(prompt);return json.dumps(bad if len(calls)==1 else a)
    result=asyncio.run(MovieSceneAnalyzer.understand(Provider(),'gemini',tmp_path/'sheet.jpg',
        {'start':0,'end':6,'frame_fingerprint':'01'*72},[],{'ai_provider':'gemini'}))
    assert result['confidence']==.95 and isinstance(result['commentary_value'],str)
    assert len(calls)==2 and 'SCHEMA REPAIR' in calls[1] and 'required_output_schema' in calls[0]
