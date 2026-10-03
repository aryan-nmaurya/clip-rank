import React, {useEffect,useState} from 'react';
import {Compass,ArrowRight,ExternalLink,RefreshCw} from 'lucide-react';
import {studioRequest} from '../api';

export function DiscoveryPage({onStartJob}:{onStartJob:(job:string,project:string)=>void}) {
  const [items,setItems]=useState<any[]>([]); const [busy,setBusy]=useState(false); const [error,setError]=useState('');
  const [warnings,setWarnings]=useState(0); const [funnel,setFunnel]=useState<any>(null);
  useEffect(()=>{studioRequest('/opportunities').then(r=>setItems(r.opportunities)).catch(e=>setError(e.message));},[]);
  async function discover() {
    setBusy(true);setError('');
    try { const r=await studioRequest('/discover',{limit:30});setItems(r.opportunities);setWarnings(r.warnings.length);setFunnel(r.funnel); }
    catch(e:any){setError(e.message);} finally{setBusy(false);}
  }
  async function produce(id:string,format='auto') {
    setBusy(true);setError('');
    try{const r=await studioRequest('/produce',{opportunity_id:id,format});onStartJob(r.job_id,r.project_id);}
    catch(e:any){setError(e.message);setBusy(false);}
  }
  return <div className="flex-1 overflow-y-auto p-8"><div className="max-w-4xl mx-auto space-y-6 pb-16">
    <div className="flex items-center gap-3"><Compass className="text-cyan-700"/><div><h1 className="text-2xl font-bold">Viral Discovery</h1><p className="text-sm text-zinc-500">Find extraordinary visual moments worth turning into Shorts.</p></div></div>
    <div className="rounded-2xl bg-cyan-950 text-white p-6 flex flex-wrap items-center justify-between gap-4"><div><h2 className="font-semibold">Extreme, unbelievable & funny moments.</h2><p className="text-sm text-cyan-100 mt-2 max-w-xl">Judge the actual moment: instant interest, visible payoff, surprise, emotion and replay value. Choose a standalone clip, related ranking or commentary Short.</p></div><button disabled={busy} onClick={discover} className="bg-white text-cyan-950 px-5 py-3 rounded-xl font-semibold text-sm disabled:opacity-50"><RefreshCw className={`inline mr-2 w-4 h-4 ${busy?'animate-spin':''}`}/>{busy?'Working…':'Discover opportunities'}</button></div>
    {error&&<p role="alert" className="bg-red-50 text-red-800 p-4 rounded-xl">{error}</p>}
    {funnel&&<p className="text-sm text-cyan-800">{funnel.discovered} found → {funnel.deep_analyzed} deeply analyzed → {funnel.verified} verified · Up to {funnel.finalists} finalists</p>}
    {busy&&<p role="status" className="text-sm text-cyan-800">Searching source videos and reviewing actual footage. Downloads and visual verification can take several minutes.</p>}
    {warnings>0&&<p className="text-sm text-amber-800">{warnings} searches or clips could not pass discovery. Only visually verified moments are shown.</p>}
    <p className="text-xs text-zinc-500">Scores describe visual moments, not guaranteed views. You manage reuse rights; publication requires a confirmed copyright check.</p>
    {!items.length&&!busy&&<div className="bg-white border rounded-2xl p-10 text-center text-zinc-500">Discover opportunities to build your next original Short.</div>}
    {items.map(item=><article key={item.id} className="bg-white border border-zinc-200 rounded-2xl p-5 space-y-3"><div className="flex justify-between gap-4"><span className="text-xs uppercase tracking-wide text-cyan-800">{item.category.replaceAll('_',' ')} · {item.opportunity_type}</span><span className="text-xs text-zinc-500">Priority {item.priority}/100</span></div><h2 className="font-semibold text-lg">{item.topic}</h2><p className="text-sm text-zinc-500">{item.reason}</p><p className="text-xs text-zinc-500">Moment: {item.moment?.start?.toFixed(1)}–{item.moment?.end?.toFixed(1)}s · Visual payoff {item.dimensions.visual_payoff}/100 · Surprise {item.dimensions.surprise}/100 · {item.rights_status.replaceAll('_',' ')}</p><div className="flex flex-wrap items-center justify-between gap-3"><a className="text-xs text-cyan-800" href={item.source_url} target="_blank" rel="noreferrer">Source footage <ExternalLink className="inline w-3 h-3"/></a><div className="flex flex-wrap gap-2">{items.filter(other=>other.category===item.category&&other.verified_topic===item.verified_topic&&other.production_ready).length>=5&&<button disabled={busy} onClick={()=>produce(item.id,'ranking')} className="rounded-xl border border-zinc-300 px-4 py-2 text-sm disabled:opacity-50">Create Top 5 · A/B</button>}<button disabled={busy||!item.production_ready} title={item.production_ready?'Generate a finished Short':'Below the production quality threshold'} onClick={()=>produce(item.id)} className="rounded-xl bg-zinc-900 text-white px-4 py-2 text-sm disabled:opacity-50">{item.production_ready?'Choose best format':'Below quality threshold'} <ArrowRight className="inline w-4 h-4 ml-1"/></button></div></div></article>)}
  </div></div>;
}
