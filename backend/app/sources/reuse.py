"""Persistent footage-use checks for approved productions and concurrent exports."""
import hashlib
import json
from contextlib import contextmanager
from urllib.parse import urlparse,parse_qs
from app.core.database import get_connection


@contextmanager
def use_connection():
    connection=get_connection()
    try:
        with connection:yield connection
    finally:connection.close()


def file_fingerprint(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def source_keys(source):
    keys=set()
    for field in ('source_sha256','source_id'):
        if source.get(field):keys.add(field+':'+str(source[field]))
    url=source.get('url') or source.get('source_url')
    if url:
        parsed=urlparse(url)
        if parsed.hostname in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be'):
            identifier=(parsed.path.strip('/') if parsed.hostname=='youtu.be' else
                parsed.path.split('/')[-1] if parsed.path.startswith(('/shorts/','/embed/')) else parse_qs(parsed.query).get('v',[''])[0])
            if identifier:keys.add('youtube:'+identifier)
        else:keys.add('url:'+url.rstrip('/'))
    return keys


def ranges(result):
    variants=result.get('variants')
    records=[m for v in variants for m in v.get('moments',[])] if variants else result.get('moments',[])
    sources={s.get('source_id') or s.get('id'):s for s in result.get('sources',[])}
    output=[]
    for record in records:
        if record.get('format') in ('DIALOGUE','COMMENTARY','AESTHETIC'):continue
        source={**sources.get(record.get('source_id'),{}),**record}
        keys=source_keys(source)
        if keys and isinstance(record.get('start'),(int,float)) and isinstance(record.get('end'),(int,float)):
            cuts=record.get('timeline') if not variants else None
            for cut in cuts or [{'source_start':record['start'],'source_end':record['end']}]:
                output.append({'keys':sorted(keys),'start':cut['source_start'],'end':cut['source_end'],'whole_source':bool(variants)})
    return output


class SourceReuse:
    @staticmethod
    def history(connection,exclude_job=None):
        output=[]
        for row in connection.execute("SELECT id,result_data FROM projects WHERE json_extract(result_data,'$.production_qc_passed')=1"):
            output.extend(ranges(json.loads(row['result_data'])))
        connection.execute('CREATE TABLE IF NOT EXISTS source_uses(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,job_id TEXT NOT NULL,data TEXT NOT NULL)')
        for row in connection.execute("SELECT u.data FROM source_uses u LEFT JOIN jobs j ON j.id=u.job_id WHERE u.job_id!=? AND (j.status IS NULL OR j.status NOT IN ('FAILED','CANCELLED'))",(exclude_job or '',)):
            output.append(json.loads(row['data']))
        return output

    @classmethod
    def used_keys(cls,project_id=None):
        with use_connection() as connection:return {key for item in cls.history(connection) for key in item['keys']}

    @classmethod
    def overlaps(cls,source,start,end,project_id=None):
        keys=source_keys(source)
        with use_connection() as connection:history=cls.history(connection)
        return any(keys.intersection(item['keys']) and (item.get('whole_source') or min(end,item['end'])-max(start,item['start'])>.05) for item in history)

    @classmethod
    def claim(cls,project_id,job_id,items):
        with use_connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            previous=cls.history(connection,exclude_job=job_id)
            for item in items:
                keys=set(item['keys'])
                if not keys:raise ValueError('Source provenance is required before exporting a Short.')
                if any(keys.intersection(old['keys']) and (item.get('whole_source') or old.get('whole_source') or min(item['end'],old['end'])-max(item['start'],old['start'])>.05) for old in previous):
                    raise ValueError('This source moment already appears in another approved Short. Choose fresh footage.')
                previous.append(item)
            for index,item in enumerate(items):
                identifier=hashlib.sha256((job_id+':'+str(index)).encode()).hexdigest()
                connection.execute('INSERT OR REPLACE INTO source_uses VALUES(?,?,?,?)',(identifier,project_id,job_id,json.dumps(item)))

    @staticmethod
    def release(job_id):
        with use_connection() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS source_uses(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,job_id TEXT NOT NULL,data TEXT NOT NULL)')
            connection.execute('DELETE FROM source_uses WHERE job_id=?',(job_id,))
