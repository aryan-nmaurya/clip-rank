"""A rights-documented user library. No scraped/trending music or implicit rights."""
import json,hashlib,shutil
import re
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
from app.core import config,database
from app.core.runtime import run_process
from app.studio.policy import RightsPolicyEngine
from app.pipelines.movie.models import MOODS

class CinematicMusicDirector:
    @staticmethod
    def library():
        with database.get_connection() as conn:
            rows=conn.execute('SELECT data FROM movie_music ORDER BY id').fetchall()
        return [json.loads(row['data']) for row in rows]
    @staticmethod
    def path(track):
        root=(config.STORAGE_DIR/'music').resolve()
        path=(root/track['filename']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.suffix!='.wav':
            raise ValueError('Licensed music is missing from managed storage.')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=track['sha256']:
            raise ValueError('Licensed music changed; import it again with its rights evidence.')
        return path
    @classmethod
    def select(cls,mood):
        for track in cls.library():
            if mood not in track.get('moods',[]):continue
            if not RightsPolicyEngine.evaluate([track['provenance']],'documented_permission')['passed']:continue
            try:cls.path(track)
            except ValueError:continue
            return track
        return None
    @classmethod
    def register(cls,source,title,creator,license,proof,moods,attribution='',rights_attested=False):
        if not rights_attested or not all(str(v).strip() for v in (title,creator,license,proof)):
            raise ValueError('Music requires commercial edited-use permission and its license/proof details.')
        if not moods or any(m not in MOODS for m in moods):raise ValueError('Choose valid movie music moods.')
        if re.search(r'cc[\s-]*by\b',license,re.I) and not attribution:
            attribution=f'{title} · {creator} · {license}'
        provenance={'source_url':proof,'source_type':'licensed_music','title':title,'creator':creator,
            'license':license,'rights_status':'commercial_use_permitted','license_evidence_url':proof,
            'fetched_at':datetime.now(timezone.utc).isoformat(),'attribution_required':bool(attribution),
            'attribution':attribution,'proof_reference':proof,'usage_rights':'User attests commercial edited audiovisual use'}
        check=RightsPolicyEngine.evaluate([provenance],'documented_permission')
        if not check['passed']:raise ValueError(check['reason'])
        # Server-chosen content identity/path; never accept a path from model output.
        identity=hashlib.sha256(source.read_bytes()+json.dumps(provenance,sort_keys=True).encode()).hexdigest()[:24]
        root=config.STORAGE_DIR/'music';root.mkdir(parents=True,exist_ok=True)
        destination=root/(identity+'.wav')
        try:
            run_process(['ffmpeg','-v','error','-y','-i',str(source),'-vn','-t','600','-ar','48000','-ac','2',
                '-af','loudnorm=I=-18:TP=-2:LRA=8','-c:a','pcm_s16le',str(destination)])
            raw=run_process(['ffmpeg','-v','error','-i',str(destination),'-ac','1','-ar','2000','-f','f32le','-'])
            if len(raw)<2000*4 or float(np.mean(np.frombuffer(raw,dtype=np.float32)**2))<1e-6:
                raise ValueError('Music is empty or inaudible.')
            track={'id':identity,'title':title,'filename':destination.name,'moods':moods,'provenance':provenance,
                'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}
            with database.get_connection() as conn:
                conn.execute('INSERT OR REPLACE INTO movie_music VALUES(?,?)',(identity,json.dumps(track)))
            return track
        except BaseException:
            destination.unlink(missing_ok=True);raise
    @classmethod
    def prepare(cls,track,folder,payoff_relative,duration):
        source=cls.path(track)
        folder.mkdir(parents=True,exist_ok=True)
        raw=run_process(['ffmpeg','-v','error','-i',str(source),'-t','120','-ac','1','-ar','2000','-f','f32le','-'])
        samples=np.frombuffer(raw,dtype=np.float32)
        size=40;usable=len(samples)//size*size
        energy=np.mean(samples[:usable].reshape(-1,size)**2,axis=1)
        flux=np.maximum(0,np.diff(energy,prepend=energy[0]))
        threshold=float(np.median(flux)+np.std(flux))
        beats=[]
        for i in np.where(flux>max(threshold,1e-7))[0]:
            time=float(i*.02)
            if not beats or time-beats[-1]>=.25:beats.append(time)
        offset=next((round(b-payoff_relative,3) for b in beats if b>=payoff_relative),0.)
        copy=folder/'licensed_music.wav'
        run_process(['ffmpeg','-v','error','-y','-stream_loop','-1','-i',str(source),'-ss',str(offset),'-t',str(duration),
            '-ar','48000','-ac','2','-c:a','pcm_s16le',str(copy)])
        return copy,{'track_name':track['title'],'source':track['provenance']['source_url'],
            'license':track['provenance']['license'],'usage_rights':track['provenance']['usage_rights'],
            'proof_reference':track['provenance']['proof_reference'],'provenance':track['provenance'],
            'offset':offset,'beats':[round(b-offset,3) for b in beats if offset<=b<=offset+duration],
            'payoff_beat_aligned':bool(beats),'method':'Measured onset peaks; offset aligns the visible payoff without retiming source action.'}
