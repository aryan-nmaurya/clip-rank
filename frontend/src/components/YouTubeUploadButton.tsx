import React, { useEffect, useState } from 'react';
import { Upload } from 'lucide-react';
import { getYouTubeConnection, getYouTubeUpload, uploadToYouTube,getCopyrightCheck,refreshCopyrightCheck,confirmCopyrightCheck,publishCopyrightCleared } from '../api';
import { OutputClip, YouTubeConnection, YouTubeUpload } from '../types';

export const YouTubeUploadButton: React.FC<{ clip: OutputClip }> = ({ clip }) => {
  const [connection, setConnection] = useState<YouTubeConnection | null>(null);
  const [upload, setUpload] = useState<YouTubeUpload | null>(null);
  const [title, setTitle] = useState(clip.title.slice(0, 100));
  const [description, setDescription] = useState('');
  const [privacy, setPrivacy] = useState('private');
  const [kids, setKids] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [check,setCheck]=useState<any>(null);
  const [reviewNote,setReviewNote]=useState('');
  const [reviewed,setReviewed]=useState(false);
  useEffect(()=>{if(upload?.status==='UPLOADED')getCopyrightCheck(clip.id).then(setCheck).catch(e=>setError(e.message));},[clip.id,upload?.status]);
  const checkAction=async(action:()=>Promise<any>)=>{setBusy(true);setError('');try{setCheck(await action());}catch(e:any){setError(e.message);}finally{setBusy(false);}};
  useEffect(() => {
    let alive = true;
    Promise.all([getYouTubeConnection(), getYouTubeUpload(clip.id)]).then(([channel, saved]) => {
      if (!alive) return;
      setConnection(channel); setUpload(saved);
      setPrivacy(saved?.metadata.privacy || channel.default_privacy);
      setKids(saved?.metadata.made_for_kids ?? channel.default_made_for_kids);
      if (saved) { setTitle(saved.metadata.title); setDescription(saved.metadata.description); }
      setLoaded(true);
    }).catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [clip.id]);
  const active = upload?.status === 'QUEUED' || upload?.status === 'UPLOADING';
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => { getYouTubeUpload(clip.id).then(setUpload).catch(e => setError(e.message)); }, 1500);
    return () => clearInterval(timer);
  }, [clip.id, active]);

  const submit = async () => {
    setBusy(true); setError('');
    try { setUpload(await uploadToYouTube(clip.id, { title, description, privacy, made_for_kids: kids })); }
    catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };
  if (clip.status !== 'READY') return null;
  return <div className="mt-3 space-y-2 text-xs">
    {upload?.status === 'UPLOADED' ? <div className="space-y-3"><div role="status" className="text-emerald-700 font-medium">Uploaded to YouTube · {upload.actual_privacy || upload.metadata.privacy}<a href={upload.url} target="_blank" rel="noreferrer" className="block underline mt-1">View uploaded video</a></div>
      <div className="border rounded-xl p-3 space-y-2"><p className="font-semibold">Copyright check · {check?.state?.replaceAll('_',' ')||'NOT CHECKED'}</p><p>{check?.message||'The upload stays private until copyright review passes.'}</p>
      {check?.state!=='PUBLISHED'&&<button disabled={busy} onClick={()=>checkAction(()=>refreshCopyrightCheck(clip.id))} className="underline">Refresh YouTube processing</button>}
      {check?.state==='PENDING_REVIEW'&&<><p>YouTube’s API cannot read the Studio copyright verdict. Review that result before confirming it here.</p><label className="flex gap-2 items-center"><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/>I reviewed the copyright checks and found no unresolved issues.</label><textarea aria-label="Copyright review evidence" value={reviewNote} onChange={e=>setReviewNote(e.target.value)} placeholder="Record the check result and review evidence" className="creator-select"/><div className="flex gap-3"><button disabled={busy||!reviewed||reviewNote.trim().length<10} onClick={()=>checkAction(()=>confirmCopyrightCheck(clip.id,'passed',reviewNote))} className="underline">Confirm copyright check passed</button><button disabled={busy||reviewNote.trim().length<10} onClick={()=>checkAction(()=>confirmCopyrightCheck(clip.id,'blocked',reviewNote))} className="underline text-red-700">Report copyright issue</button></div></>}
      {check?.state==='PASSED'&&<button disabled={busy} onClick={()=>checkAction(async()=>{setUpload(await publishCopyrightCleared(clip.id));return getCopyrightCheck(clip.id);})} className="w-full bg-zinc-900 text-white rounded-xl p-2">Release cleared video · {upload.metadata.privacy}</button>}
      </div></div> : <>
      <p className="text-[11px] text-zinc-500">{connection?.connected ? `${connection.channel_title} · ${privacy} · ${kids ? 'Made for kids' : 'Not made for kids'}` : loaded ? 'Connect your YouTube channel in Settings.' : 'Checking YouTube connection…'}</p>
      <details><summary className="cursor-pointer text-zinc-600">Upload details</summary><div className="space-y-2 mt-2">
        <label className="block">Title<input aria-label="YouTube video title" value={title} maxLength={100} disabled={!!upload} onChange={e => setTitle(e.target.value)} className="creator-select mt-1" /></label>
        <label className="block">Description<textarea aria-label="YouTube video description" value={description} disabled={!!upload} onChange={e => setDescription(e.target.value)} className="creator-select mt-1 min-h-20" /></label>
        <label className="block">Visibility<select aria-label="YouTube video visibility" className="creator-select mt-1" value={privacy} disabled={!!upload} onChange={e => setPrivacy(e.target.value)}><option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label>
        <label className="block">Audience<select aria-label="YouTube video audience" className="creator-select mt-1" value={String(kids)} disabled={!!upload} onChange={e => setKids(e.target.value === 'true')}><option value="false">Not made for kids</option><option value="true">Made for kids</option></select></label>
        <p className="text-[11px] text-zinc-500">Uploads start privately for copyright checks. Your selected visibility is applied only after clearance. Source credits are added automatically; retries preserve the original upload.</p>
      </div></details>
      <button type="button" disabled={!loaded || !connection?.connected || busy || active || !title.trim()} onClick={submit} className="w-full h-9 rounded-xl bg-red-600 hover:bg-red-700 text-white font-medium flex items-center justify-center gap-2 disabled:opacity-50"><Upload className="w-4 h-4" />{active ? `Uploading ${upload?.progress || 0}%` : busy ? 'Starting upload…' : upload?.status === 'FAILED' ? 'Retry upload' : 'Upload to YouTube'}</button>
      {active && <div role="progressbar" aria-valuenow={upload?.progress || 0} aria-valuemin={0} aria-valuemax={100} className="h-1 bg-zinc-100 rounded-full overflow-hidden"><div className="bg-red-600 h-full" style={{ width: `${upload?.progress || 0}%` }} /></div>}
      {upload?.error && <p role="alert" className="text-red-600">{upload.error}</p>}
    </>}
    {error && <p role="alert" className="text-red-600">{error}</p>}
  </div>;
};
