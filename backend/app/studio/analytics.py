"""Official APIs only; unavailable metrics stay unavailable."""
import json
from datetime import datetime, timedelta, timezone
import requests
from app.core.database import get_connection, get_project
from app.publishing import youtube
from app.studio.models import now


class AnalyticsCollector:
    @staticmethod
    def collect():
        connection=youtube.connection_status()
        if not connection['connected']: return {'collected':0,'message':'Connect YouTube to collect real performance data.'}
        token=youtube._access_token(connection['channel_id'])
        headers={'Authorization':'Bearer '+token}
        with get_connection() as c:
            uploads=c.execute("SELECT clip_id,video_id FROM youtube_uploads WHERE status='UPLOADED'").fetchall()
        if not uploads: return {'collected':0,'message':'No published ClipRank videos yet.'}
        count=0
        for upload in uploads:
            video_id=upload['video_id']
            from app.core.database import get_clip
            clip=get_clip(upload['clip_id']); project=get_project(clip['project_id']) if clip else {}
            result=project.get('result_data',{})
            data={'video_id':video_id,'topic':project.get('title'),'category':result.get('pillar','manual'),
                  'format':result.get('format',project.get('mode')),'duration':clip.get('duration') if clip else None,
                  'voice':result.get('voice_profile'),'averageViewPercentage':None,'subscribersGained':None}
            for dimension in ('hook_type','visual_structure','cta_style','source_type'):
                data[dimension]=result.get(dimension)
            response=requests.get(youtube.API_URL+'/videos',params={'part':'statistics','id':video_id},headers=headers,timeout=25)
            youtube._check_response(response)
            items=response.json().get('items',[])
            if not items: continue
            statistics=items[0].get('statistics',{})
            for api,key in [('viewCount','views'),('likeCount','likes'),('commentCount','comments')]:
                data[key]=int(statistics[api]) if api in statistics else None
            end=datetime.now(timezone.utc).date()-timedelta(days=2)
            start=end-timedelta(days=28)
            analytics=requests.get('https://youtubeanalytics.googleapis.com/v2/reports',headers=headers,timeout=25,params={
                'ids':'channel==MINE','startDate':start.isoformat(),'endDate':end.isoformat(),'filters':'video=='+video_id,
                'metrics':'views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained',
                'dimensions':'video'})
            if analytics.status_code==200:
                body=analytics.json(); rows=body.get('rows',[])
                if rows:
                    names=[column['name'] for column in body['columnHeaders']]
                    data.update(dict(zip(names,rows[0])))
                    data['analytics_status']='available'
                else: data['analytics_status']='not_available_yet'
            elif analytics.status_code in (401,403):
                data['analytics_status']='Enable YouTube Analytics API and reconnect with analytics permission.'
            else:
                data['analytics_status']='Temporarily unavailable; retry in the next collection cycle.'
            with get_connection() as c:
                c.execute('INSERT INTO performance VALUES(?,?,?) ON CONFLICT(video_id) DO UPDATE SET data=excluded.data,collected_at=excluded.collected_at',
                          (video_id,json.dumps(data),now()))
                if data.get('analytics_status')=='available':
                    c.execute("UPDATE studio_tasks SET status='COMPLETE',stage='COMPLETE' WHERE video_id=? AND status='ANALYTICS_PENDING'",(video_id,))
            count+=1
        return {'collected':count}


def dashboard_performance():
    with get_connection() as c:
        rows=[json.loads(row['data']) for row in c.execute('SELECT data FROM performance').fetchall()]
        published=c.execute("SELECT count(*) AS n FROM youtube_uploads WHERE status='UPLOADED' AND updated_at>=?",
                            ((datetime.now(timezone.utc)-timedelta(days=7)).isoformat(),)).fetchone()['n']
    # These are observed totals, not fabricated last-seven-days counters.
    return {'observed_views':sum(row.get('views') or 0 for row in rows) if rows else None,
            'observed_subscribers_gained':sum(row.get('subscribersGained') or 0 for row in rows) if any(row.get('subscribersGained') is not None for row in rows) else None,
            'published_last_7_days':published,'metrics_window':'Per-video available reporting window; values may lag.'}
