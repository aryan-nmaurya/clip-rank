"""Conservative speaker/subject tracking; retain the wide frame when a crop loses action."""
import math,json,re
import numpy as np
from pathlib import Path
from pydantic import BaseModel,ConfigDict,Field
from app.core.runtime import check_cancelled,run_blocking,run_process
from app.ai.ranking_verifier import parse_object

class MovieCropSubject(BaseModel):
    model_config=ConfigDict(extra='forbid')
    time:float=Field(ge=0,allow_inf_nan=False)
    center_x:float=Field(ge=0,le=1,allow_inf_nan=False)
    subject:str=Field(min_length=5,max_length=250)

class MovieCropReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    subjects:list[MovieCropSubject]=Field(min_length=1,max_length=32)
    confidence:float=Field(ge=0,le=1,allow_inf_nan=False)

class MovieReframing:
    @staticmethod
    async def refine(provider,video,start,end,folder,base):
        """Vision selects the story subject; local tracking supplies source geometry."""
        if base.get('layout')!='tracked' or not hasattr(provider,'analyze_images'):return base
        from PIL import Image,ImageDraw
        from app.media.ffmpeg_core import FFmpegCore
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        duration=end-start
        changes=await run_blocking(run_process,['ffmpeg','-hide_banner','-ss',str(start),'-i',str(video),'-t',str(duration),
            '-vf',"setpts=PTS-STARTPTS,select='gt(scene,0.18)',showinfo",'-an','-f','null','-'],include_stderr=True)
        cuts=sorted({float(t) for t in re.findall(r'pts_time:([0-9.]+)',changes.decode(errors='replace'))
            if .15<float(t)<duration-.15})
        if len(cuts)>15:raise ValueError('This moment has too many shot changes for a reliable mobile subject track.')
        bounds=[0.,*cuts,duration];samples=[]
        for left,right in zip(bounds,bounds[1:]):
            if right-left<.15:continue
            count=max(2,math.ceil((right-left)/2.5)+1)
            for idx,time in enumerate(np.linspace(left+min(.05,(right-left)/4),right-min(.08,(right-left)/4),count)):
                samples.append({'time':round(float(time),3),'track_time':round(left if idx==0 else float(time),3),
                    'cut':idx==0 and left>0})
        if len(samples)>32:
            # Keep every shot's two boundary samples; longest shots share the
            # remaining budget for nonlinear movement within their composition.
            boundary={i for i,s in enumerate(samples) if s['cut'] or i==0 or i==len(samples)-1
                or i+1<len(samples) and samples[i+1]['cut']}
            optional=[i for i in range(len(samples)) if i not in boundary]
            keep=sorted(boundary|{optional[int(i)] for i in np.linspace(0,len(optional)-1,32-len(boundary))})
            samples=[samples[i] for i in keep]
        times=[s['time'] for s in samples]
        sheets=[]
        for first in range(0,len(times),4):
            group=times[first:first+4];sheet=Image.new('RGB',(960,580*len(group)),'#222222');draw=ImageDraw.Draw(sheet)
            for row,time in enumerate(group):
                path=await run_blocking(FFmpegCore.extract_frame,video,folder/f'subject_{first+row}.jpg',start+time)
                frame=Image.open(path);frame.thumbnail((960,540));sheet.paste(frame,((960-frame.width)//2,row*580))
                draw.text((20,row*580+550),f'CLIP TIME {time:.3f} SECONDS; ORIGINAL FRAME X: 0% LEFT, 50% CENTER, 100% RIGHT',fill='white')
            path=folder/f'subject_sheet_{first}.jpg';sheet.save(path);sheets.append(path)
        prompt=('Locate the MAIN STORY SUBJECT in every original widescreen movie frame. We will crop to a narrow 9:16 '
            'portrait window. Return its horizontal center_x as a fraction of ORIGINAL image width, 0 left to 1 right. '
            'Choose the face during a reaction, the injured creature during its reveal, and the main moving actor during action. '
            'Never choose a background wall, roof tiles, shadows, staff, or foreground obstruction instead of the subject. '
            'If a person is partly obscured choose the visible face/body. At a creature close-up prioritize the creature, '
            'even when a human stands near an edge. Do not guess the plot. Return one subject for EVERY supplied numeric '
            'clip time, in order, with a short description of the visible subject.\n'+json.dumps({'times':times,
                'required_output_schema':MovieCropReview.model_json_schema()}))
        error=None
        for attempt in range(2):
            raw=await provider.analyze_images(sheets,prompt+('\nSCHEMA REPAIR: '+str(error) if attempt else ''),options={'json':True})
            try:
                value=MovieCropReview.model_validate(parse_object(raw)).model_dump()
                if value['confidence']<.85 or len(value['subjects'])!=len(times):raise ValueError('All crop samples require confident visible subjects.')
                info=await run_blocking(FFmpegCore.get_video_info,video);width=info['width'];crop=base['crop_width'];points=[]
                for time,sample,subject in zip(times,samples,value['subjects']):
                    if abs(subject['time']-time)>.01:raise ValueError('Crop times must match the supplied samples exactly.')
                    x=max(0,min(width-crop,subject['center_x']*width-crop/2))
                    points.append({'time':sample['track_time'],'x':round(x,2),'cut':sample['cut']})
                points[0]['time']=0.
                return {**base,'points':points,'subject_review':value,'scene_cuts':cuts,
                    'reason':'Original-frame vision locates the story subject for the mobile crop; source geometry is measured locally.'}
            except (ValueError,TypeError,KeyError) as exc:error=str(exc)
        raise ValueError('Could not confirm a safe mobile crop: '+str(error)[-300:])

    @staticmethod
    def plan(video,start,end,format,portrait=False,focus='faces'):
        try:import cv2
        except ImportError:
            if portrait:raise ValueError('Install the configured OpenCV dependency for subject-aware mobile cropping.')
            return {'layout':'fit','reason':'Full-frame layout preserves all important subjects.'}
        capture=cv2.VideoCapture(str(video))
        width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH));height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        crop_width=round(height*9/16)
        if width<=crop_width:
            capture.release();return {'layout':'fill' if portrait else 'fit','reason':'Source already fits a portrait canvas.'}
        cascade=cv2.CascadeClassifier(cv2.data.haarcascades+'haarcascade_frontalface_default.xml')
        points=[];previous=None
        try:
            for time in np.linspace(start,end-.08,min(32,max(6,int((end-start)*2)))):
                check_cancelled();capture.set(cv2.CAP_PROP_POS_MSEC,float(time)*1000)
                ok,frame=capture.read()
                if not ok:
                    if portrait:raise ValueError('A mobile crop tracking sample could not be decoded.')
                    return {'layout':'fit','reason':'Tracking sample could not be decoded; preserve full frame.'}
                small=cv2.resize(frame,(480,max(1,round(height*480/width))))
                gray=cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
                faces=cascade.detectMultiScale(gray,scaleFactor=1.1,minNeighbors=4,minSize=(20,20))
                scale=width/480
                if portrait:
                    difference=cv2.absdiff(gray,previous) if previous is not None else np.zeros_like(gray)
                    shot_change=previous is not None and float(difference.mean())>45
                    if len(faces) and (focus=='faces' or previous is None):
                        face=max(faces,key=lambda f:float(f[2]*f[3])/(1+abs(f[0]+f[2]/2-240)/240))
                        center=float(face[0]+face[2]/2)*scale
                    else:
                        # Choose the crop window containing the most foreground
                        # movement, rather than centering between distant actors.
                        activity=difference.astype(float);activity[activity<18]=0
                        if activity.sum()<1000 or shot_change:
                            activity=cv2.Canny(gray,70,160).astype(float)
                        columns=activity.sum(axis=0)
                        span=max(1,min(480,round(crop_width/scale)))
                        strengths=np.convolve(columns,np.ones(span),'valid')
                        centers=np.arange(len(strengths))+span/2
                        reference=(points[-1]['x']+crop_width/2)/scale if points and not shot_change else 240
                        strengths*=.65+.35*np.exp(-((centers-reference)/150)**2)
                        center=(float(np.argmax(strengths))+span/2)*scale
                    x=max(0,min(width-crop_width,center-crop_width/2))
                    if points and not shot_change:x=points[-1]['x']*.3+x*.7
                    points.append({'time':round(float(time-start),3),'x':round(x,2)})
                    previous=gray;continue
                if len(faces):
                    # All visible faces must fit. The most active lower face helps
                    # choose the speaker, but never discard the other participant.
                    lo=min(float(f[0]) for f in faces)*scale;hi=max(float(f[0]+f[2]) for f in faces)*scale
                    if hi-lo>crop_width*.8:return {'layout':'fit','reason':'Multiple characters need the wide composition.'}
                    center=(lo+hi)/2
                elif format=='DIALOGUE':return {'layout':'fit','reason':'Speaker identity is uncertain; preserve both characters.'}
                elif previous is not None:
                    change=cv2.absdiff(gray,previous)
                    change[change<18]=0
                    columns=np.sum(change,axis=0).astype(float)
                    if columns.sum()<1000:return {'layout':'fit','reason':'Wide cinematic composition has no confidently isolated subject.'}
                    distribution=np.cumsum(columns)/columns.sum()
                    lo=float(np.searchsorted(distribution,.1))*scale;hi=float(np.searchsorted(distribution,.9))*scale
                    if hi-lo>crop_width*.8:return {'layout':'fit','reason':'Action spans the wide frame; crop would lose it.'}
                    center=(lo+hi)/2
                else:
                    previous=gray;continue
                previous=gray
                x=max(0,min(width-crop_width,center-crop_width/2))
                if points:
                    # Smooth only within the measured subject's safe crop range.
                    safe_lo=max(0,hi-crop_width*.9);safe_hi=min(width-crop_width,lo-crop_width*.1)
                    if safe_lo>safe_hi:return {'layout':'fit','reason':'No safe subject crop exists.'}
                    x=max(safe_lo,min(safe_hi,points[-1]['x']*.5+x*.5))
                points.append({'time':round(float(time-start),3),'x':round(x,2)})
        finally:capture.release()
        if not points:
            if portrait:raise ValueError('No reliable mobile subject crop could be measured.')
            return {'layout':'fit','reason':'No reliable subject track.'}
        points[0]['time']=0.
        return {'layout':'tracked','crop_width':crop_width,'points':points,
            'reason':'Mobile frame filled using measured face/motion focus with shot-aware tracking.' if portrait else 'Measured face/action bounds retained with smoothed subject tracking.'}
