import React, {useState} from 'react';
import {Clapperboard, Upload, Loader2, ChevronDown, Link} from 'lucide-react';
import {createMovieProject, importMovieMusic} from '../api';

export const MoviePage:React.FC<{onStartJob:(job:string,project:string)=>void}>=({onStartJob})=>{
  const [url,setUrl]=useState(''); const [file,setFile]=useState<File|null>(null);
  const [advanced,setAdvanced]=useState(false); const [count,setCount]=useState(3); const [duration,setDuration]=useState(30);
  const [provider,setProvider]=useState('global'); const [authorized,setAuthorized]=useState(false);
  const [layout,setLayout]=useState('fill');
  const [title,setTitle]=useState(''); const [creator,setCreator]=useState(''); const [license,setLicense]=useState('');
  const [proof,setProof]=useState(''); const [attribution,setAttribution]=useState('');
  const [music,setMusic]=useState<File|null>(null); const [musicTitle,setMusicTitle]=useState('');
  const [musicCreator,setMusicCreator]=useState(''); const [musicLicense,setMusicLicense]=useState('');
  const [musicProof,setMusicProof]=useState(''); const [mood,setMood]=useState('action');const [musicAuthorized,setMusicAuthorized]=useState(false);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const input='w-full rounded-xl border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-violet-200';
  async function submit(e:React.FormEvent){
    e.preventDefault();setError('');
    if(!url.trim()&&!file){setError('Paste an authorized movie URL or upload a video.');return;}
    if(!authorized){setError('Confirm that you own the footage or have permission to reuse its video and audio.');return;}
    setBusy(true);
    try{
      if(music){
        const data=new FormData(); data.append('audio_file',music);data.append('title',musicTitle||music.name);
        data.append('creator',musicCreator);data.append('license',musicLicense);data.append('proof_reference',musicProof);
        data.append('moods',mood);data.append('rights_attested',String(musicAuthorized));
        await importMovieMusic(data);
      }
      const data=new FormData();if(file)data.append('video_file',file);else data.append('video_url',url.trim());
      for(const [key,value] of Object.entries({count:String(count),target_duration:String(duration),ai_provider:provider,
        authorization_attested:'true',source_audio_authorized:'true',source_title:title,source_creator:creator,
        source_license:license,license_reference:proof,source_attribution:attribution,language:'en',movie_layout:layout}))data.append(key,value);
      const result=await createMovieProject(data);onStartJob(result.job_id,result.project_id);
    }catch(err:any){setError(err.message||'Could not start movie production.');}finally{setBusy(false);}
  }
  return <div className="flex-1 h-screen overflow-y-auto bg-[#FAF9F6] p-8 flex justify-center"><div className="max-w-2xl w-full flex flex-col gap-6 pb-12">
    <div><div className="inline-flex items-center gap-2 rounded-full bg-violet-50 px-3 py-1 text-xs text-violet-700 font-semibold"><Clapperboard size={14}/>Movie / Trailer Moments</div>
      <h1 className="text-3xl font-bold tracking-tight mt-3">Let the scene do the talking.</h1>
      <p className="text-sm text-zinc-500 mt-2">Turn authorized cinematic footage into finished Shorts. ClipRank chooses dialogue, original commentary, or a cinematic edit for each strong moment.</p></div>
    <form onSubmit={submit} className="flex flex-col gap-5">
      <div className="rounded-2xl bg-white border border-zinc-200 p-6 space-y-4">
        <label className="block text-sm font-semibold" htmlFor="movie-url">Movie or trailer</label>
        <div className="relative"><Link className="absolute left-3 top-3.5 text-zinc-400" size={16}/><input id="movie-url" className={input+' pl-10'} placeholder="Paste authorized video URL" value={url} onChange={e=>{setUrl(e.target.value);if(e.target.value)setFile(null);}}/></div>
        <div className="text-center text-xs text-zinc-400">or</div>
        <label className="cursor-pointer rounded-xl border border-dashed border-zinc-300 p-6 flex flex-col items-center gap-2 text-sm"><Upload size={20} className="text-violet-500"/>{file?file.name:'Upload Video'}<input aria-label="Upload movie video" className="sr-only" type="file" accept="video/*,.mkv" onChange={e=>{setFile(e.target.files?.[0]||null);setUrl('');}}/></label>
        <label className="flex gap-2 text-xs text-zinc-600 leading-relaxed"><input type="checkbox" className="mt-0.5 accent-violet-700" checked={authorized} onChange={e=>setAuthorized(e.target.checked)}/>I own this source or have permission to create and publish edited clips, including its audio.</label>
      </div>
      <div className="rounded-2xl border border-zinc-200 bg-white overflow-hidden"><button type="button" aria-expanded={advanced} onClick={()=>setAdvanced(!advanced)} className="w-full p-4 flex justify-between text-sm font-medium">Advanced <ChevronDown size={16}/></button>
      {advanced&&<div className="px-5 pb-5 space-y-4">
        <div className="grid grid-cols-2 gap-4"><label className="text-xs font-medium space-y-1 block">Outputs<select className={input} value={count} onChange={e=>setCount(Number(e.target.value))}>{[1,2,3,4,5].map(n=><option key={n}>{n}</option>)}</select></label>
          <label className="text-xs font-medium space-y-1 block">Target duration<select className={input} value={duration} onChange={e=>setDuration(Number(e.target.value))}>{[15,20,25,30,35,45].map(n=><option key={n} value={n}>{n} seconds</option>)}</select></label></div>
        <label className="text-xs font-medium block">AI mode<select className={input} value={provider} onChange={e=>setProvider(e.target.value)}><option value="global">Use Settings</option><option value="auto">Automatic</option><option value="local">Local only</option><option value="gemini">Google AI Studio only</option></select></label>
        <label className="text-xs font-medium block">Framing<select className={input} value={layout} onChange={e=>setLayout(e.target.value)}><option value="fill">Fill mobile screen · 9:16 crop</option><option value="fit">Preserve wide composition</option></select></label>
        <p className="text-xs text-zinc-500">English speech and captions. Output counts are a maximum; only complete moments that pass review are returned.</p>
        <div className="border-t pt-4 space-y-2"><p className="text-sm font-semibold">Source credits and permission</p>
          <p className="text-xs text-zinc-500">Credits are kept in the description and metadata, without copyright text on the video.</p>
          <input aria-label="Movie title" className={input} placeholder="Movie / trailer title" value={title} onChange={e=>setTitle(e.target.value)}/>
          <input aria-label="Source creator" className={input} placeholder="Creator / studio" value={creator} onChange={e=>setCreator(e.target.value)}/>
          <input aria-label="Source license" className={input} placeholder="License or permission" value={license} onChange={e=>setLicense(e.target.value)}/>
          <input aria-label="License evidence" className={input} placeholder="License / permission reference" value={proof} onChange={e=>setProof(e.target.value)}/>
          <input aria-label="Attribution credit" className={input} placeholder="Attribution for description" value={attribution} onChange={e=>setAttribution(e.target.value)}/></div>
        <details className="border-t pt-4"><summary className="text-sm font-semibold cursor-pointer">Add licensed music to your library</summary><div className="space-y-2 mt-3">
          <p className="text-xs text-zinc-500">Cinematic edits can use authorized source audio. Optional tracks need documented commercial edited-use rights; they are reused only for matching scene moods.</p>
          <input aria-label="Licensed music file" type="file" accept="audio/*" onChange={e=>setMusic(e.target.files?.[0]||null)}/>
          <input aria-label="Music title" className={input} placeholder="Track name" value={musicTitle} onChange={e=>setMusicTitle(e.target.value)}/>
          <input aria-label="Music creator" className={input} placeholder="Music creator" value={musicCreator} onChange={e=>setMusicCreator(e.target.value)}/>
          <input aria-label="Music license" className={input} placeholder="Music license" value={musicLicense} onChange={e=>setMusicLicense(e.target.value)}/>
          <input aria-label="Music license proof" className={input} placeholder="License / purchase / permission reference" value={musicProof} onChange={e=>setMusicProof(e.target.value)}/>
          <label className="text-xs">Scene mood<select className={input} value={mood} onChange={e=>setMood(e.target.value)}>{['dominance','action','epic','style','dark','emotional','funny','dreamy'].map(m=><option key={m}>{m}</option>)}</select></label>
          <label className="flex gap-2 text-xs"><input type="checkbox" checked={musicAuthorized} onChange={e=>setMusicAuthorized(e.target.checked)}/>This license permits commercial use in edited audiovisual content.</label>
        </div></details>
      </div>}</div>
      {error&&<p role="alert" className="text-sm text-red-600">{error}</p>}
      <button type="submit" disabled={busy} className="rounded-xl bg-violet-950 hover:bg-violet-900 disabled:opacity-50 text-white px-6 py-3 text-sm font-semibold flex justify-center items-center gap-2">{busy?<Loader2 className="animate-spin" size={16}/>:<Clapperboard size={16}/>} {busy?'Starting production…':'Generate Moments'}</button>
    </form><p className="text-xs text-zinc-400 text-center">Complete scenes. Natural audio. Finished vertical MP4s.</p>
  </div></div>;
};
