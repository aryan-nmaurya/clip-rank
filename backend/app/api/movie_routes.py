"""Local movie jobs and a documented music library; no credentials in UI payloads."""
import json,uuid
from pathlib import Path
from fastapi import APIRouter,Depends,Form,File,UploadFile,HTTPException,Request
from fastapi.responses import FileResponse
from app.api.youtube_routes import local_access,local_mutation
from app.api.routes import save_upload,enqueue_project,validate_provider,validate_urls
from app.core import config,database
from app.core.runtime import run_blocking
from app.storage.manager import StorageManager
from app.studio import store
from app.pipelines.movie.movie_pipeline import MoviePipeline
from app.pipelines.movie.music import CinematicMusicDirector

router=APIRouter()

def local_form_mutation(request:Request):
    local_access(request)
    allowed={f'http://{host}:{port}' for host in ('localhost','127.0.0.1') for port in (8000,5173)}
    origin=request.headers.get('origin')
    if (origin and origin not in allowed) or request.headers.get('sec-fetch-site')=='cross-site':
        raise HTTPException(403,'Movie actions are only available from the local app.')
    if not any(kind in request.headers.get('content-type','') for kind in ('multipart/form-data','application/x-www-form-urlencoded')):
        raise HTTPException(415,'Send a movie upload form.')

@router.post('/projects/movie',dependencies=[Depends(local_form_mutation)])
async def create_movie(video_url:str=Form(''),video_file:UploadFile|None=File(None),count:int=Form(3),
    target_duration:float=Form(30),ai_provider:str=Form('global'),language:str=Form('en'),
    authorization_attested:bool=Form(False),source_title:str=Form(''),source_creator:str=Form(''),
    source_license:str=Form(''),license_reference:str=Form(''),source_attribution:str=Form(''),
    source_audio_authorized:bool=Form(True),movie_layout:str=Form('fill')):
    if not authorization_attested:raise HTTPException(400,'Confirm that you own the source or have permission covering edited video and its audio.')
    if not source_audio_authorized:raise HTTPException(400,'Movie source permission must include its audio; muting does not establish reuse rights.')
    if not 1<=count<=5 or not 12<=target_duration<=45:raise HTTPException(400,'Choose 1–5 movie outputs and a 12–45 second target.')
    if language!='en':raise HTTPException(400,'Movie production currently supports accurate English speech/captions. Choose English.')
    if movie_layout not in ('fill','fit'):raise HTTPException(400,'Choose a mobile crop or preserved wide composition.')
    if ai_provider!='global':validate_provider(ai_provider)
    if any(len(v)>1500 for v in (source_title,source_creator,source_license,license_reference,source_attribution)):
        raise HTTPException(400,'Source rights details are too long.')
    url=video_url.strip();has_upload=bool(video_file and video_file.filename)
    if bool(url)==has_upload:raise HTTPException(400,'Provide one authorized video URL or one uploaded file.')
    if url:url=validate_urls([url])[0]
    policy=store.profile().rights_policy
    if policy=='documented_permission' and not (source_license.strip() and license_reference.strip() and source_creator.strip()):
        raise HTTPException(400,'Your source policy requires license, creator and permission/license evidence in Advanced.')
    settings=database.get_settings()
    if ai_provider!='global':settings['ai_provider']=ai_provider
    from app.ai.router import AIRouter
    provider,_=await AIRouter.get_active_provider(settings)
    if not provider:raise HTTPException(400,'Connect vision in Settings before generating movie moments.')
    project_id='proj_m_'+uuid.uuid4().hex[:12]
    job_id='job_'+uuid.uuid4().hex[:12]
    try:
        source=url if url else await save_upload(video_file,project_id,job_id=job_id)
        title=source_title.strip() or (Path(video_file.filename).stem if has_upload else 'Movie / Trailer')
        data={'video_source':source,'video_url':url or None,'count':count,'target_duration':target_duration,
            'language':language,'authorization_attested':True,'source_audio_authorized':source_audio_authorized,
            'source_title':title,'source_creator':source_creator.strip(),'source_license':source_license.strip(),
            'license_reference':license_reference.strip(),'source_attribution':source_attribution.strip(),'rights_policy':policy,'movie_layout':movie_layout}
        if ai_provider!='global':data['ai_provider']=ai_provider
        return enqueue_project(project_id,'movie','Movie moments · '+title,data,job_id)
    except BaseException:
        StorageManager.cleanup_job_temp(job_id)
        raise

@router.get('/movie/music',dependencies=[Depends(local_access)])
def movie_music():
    return [{k:v for k,v in track.items() if k not in ('filename','sha256')} for track in CinematicMusicDirector.library()]

@router.post('/movie/music',dependencies=[Depends(local_form_mutation)])
async def import_music(audio_file:UploadFile=File(...),title:str=Form(...),creator:str=Form(...),
    license:str=Form(...),proof_reference:str=Form(...),moods:str=Form('action'),attribution:str=Form(''),
    rights_attested:bool=Form(False)):
    if any(len(v)>1500 for v in (title,creator,license,proof_reference,attribution)):
        raise HTTPException(400,'Music license details are too long.')
    suffix=Path(audio_file.filename or '').suffix.lower()
    if suffix not in ('.wav','.mp3','.m4a','.aac','.ogg','.flac'):raise HTTPException(400,'Import a playable audio file.')
    identifier='music_import_'+uuid.uuid4().hex[:12]
    folder=StorageManager.get_job_temp_dir(identifier);path=folder/('upload'+suffix)
    try:
        size=0
        with path.open('wb') as handle:
            while chunk:=await audio_file.read(1024*1024):
                size+=len(chunk)
                if size>100*1024**2:raise HTTPException(413,'Music imports must be at most 100 MB.')
                handle.write(chunk)
        track=await run_blocking(CinematicMusicDirector.register,path,title,creator,license,proof_reference,
            list(dict.fromkeys(m.strip() for m in moods.split(',') if m.strip())),attribution,rights_attested)
        return {k:v for k,v in track.items() if k not in ('filename','sha256')}
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    finally:
        await audio_file.close();StorageManager.cleanup_job_temp(identifier)

@router.get('/projects/{project_id}/movie-metadata',dependencies=[Depends(local_access)])
def movie_metadata(project_id:str):
    project=database.get_project(project_id)
    if not project or project['mode']!='movie' or not project['result_data'].get('production_qc_passed'):
        raise HTTPException(404,'Approved movie metadata was not found.')
    path=(config.MOVIE_OUTPUT_DIR/(project_id+'_metadata.json')).resolve()
    if not path.is_relative_to(config.MOVIE_OUTPUT_DIR.resolve()) or not path.is_file():raise HTTPException(404,'Movie metadata was not found.')
    return FileResponse(path,media_type='application/json',filename='metadata.json')
