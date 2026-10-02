"""HTTPS polling only. The paired server can supply typed plans, never shell commands or file paths."""
import json
from urllib.parse import urlparse
import requests
from app.core.secrets import SecretVault
from app.studio import store
from app.studio.models import ChannelProfile


class CloudBridge:
    def __init__(self): self.current=None

    @staticmethod
    def configuration():
        if not SecretVault.ready(): return None
        value=SecretVault.get('paired-control-plane')
        return json.loads(value) if value else None

    @staticmethod
    def pair(url,token):
        parsed=urlparse(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Use the HTTPS origin of your deployed ClipRank dashboard.')
        if parsed.path not in ('','/') or len(token)<32: raise ValueError('Use a dashboard origin and valid pairing token.')
        url=url.rstrip('/')
        response=requests.get(url+'/api/worker',headers={'Authorization':'Bearer '+token},timeout=15,allow_redirects=False)
        if response.status_code!=200: raise ValueError('Pairing failed. Check the dashboard and token.')
        ChannelProfile.model_validate(response.json()['profile'])
        SecretVault.set('paired-control-plane',json.dumps({'url':url,'token':token}))

    def request(self,method,data=None):
        config=self.configuration()
        if not config: return None
        response=requests.request(method,config['url']+'/api/worker',headers={'Authorization':'Bearer '+config['token']},
                                  json=data,timeout=15,allow_redirects=False)
        if response.status_code!=200: raise ValueError('Cloud control connection is unavailable; existing local jobs are retained.')
        return response.json()

    def poll(self):
        result=self.request('GET')
        if result is None: return None
        store.save_profile(ChannelProfile.model_validate(result['profile']))
        job=result.get('job')
        if job:
            if job.get('kind')!='PLAN_DAY' or job.get('payload')!={}: raise ValueError('Unsupported cloud job. No command or path is executed.')
            self.current=job
        return result

    def heartbeat(self,status=None):
        tasks=store.tasks()
        summary={'online':True,'queue':sum(t['status']=='CREATED' for t in tasks),'current_job':next((t['id'] for t in tasks if t['status']=='PROCESSING'),None),
                 'tasks':[{'id':t['id'],'title':t['checkpoint'].get('result',{}).get('metadata',{}).get('title','Visual entertainment Short'),
                           'status':t['status'],'video_id':t['video_id']} for t in tasks[:20]]}
        data={'summary':summary}
        if self.current: data.update(job_id=self.current['id'],status=status or 'PROCESSING')
        result=self.request('POST',data)
        if status in ('COMPLETE','FAILED'): self.current=None
        return result
