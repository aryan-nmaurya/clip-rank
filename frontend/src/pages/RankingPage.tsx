import React, { useEffect, useState } from 'react';
import { Award, ArrowRight, Loader2, Search, Link, Upload, Volume2, Film } from 'lucide-react';
import { createRankingProject, createRankingUpload } from '../api';

type SourceMode = 'discover' | 'links' | 'upload';

export const RankingPage: React.FC<{ onStartJob: (jobId: string, projectId: string) => void }> = ({ onStartJob }) => {
  const [topic, setTopic] = useState('');
  const [count, setCount] = useState(5);
  const [provider, setProvider] = useState('auto');
  const [sourceMode, setSourceMode] = useState<SourceMode>('discover');
  const [links, setLinks] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [narration, setNarration] = useState(false);
  const [layout, setLayout] = useState<'fill' | 'fit'>('fill');
  const [seconds, setSeconds] = useState(7);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const sourceUrls = [...new Set(links.split(/\n/).map(s => s.trim()).filter(Boolean))];

  useEffect(() => {
    const match = topic.match(/\b(?:top|best)\s*(\d{1,2})\b/i);
    if (match && Number(match[1]) >= 3 && Number(match[1]) <= 10) setCount(Number(match[1]));
  }, [topic]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    if (sourceMode === 'links' && sourceUrls.length < count) return setError(`Add at least ${count} distinct video links for Top ${count}.`);
    if (sourceMode === 'upload' && files.length < count) return setError(`Select at least ${count} different source videos.`);
    setLoading(true);
    try {
      let response;
      if (sourceMode === 'upload') {
        const data = new FormData();
        files.forEach(file => data.append('video_files', file));
        data.append('topic', topic.trim()); data.append('count', String(count));
        data.append('ai_provider', provider); data.append('narration', String(narration));
        data.append('layout', layout); data.append('segment_duration', String(seconds));
        response = await createRankingUpload(data);
      } else {
        response = await createRankingProject({ topic: topic.trim(), count, ai_provider: provider,
          source_urls: sourceMode === 'links' ? sourceUrls : [], narration, layout, segment_duration: seconds });
      }
      onStartJob(response.job_id, response.project_id);
    } catch (err: any) { setError(err.message || 'Could not start ranking video.'); }
    finally { setLoading(false); }
  };

  return <div className="flex-1 overflow-y-auto p-6 lg:p-10 bg-[#FAF9F6]">
    <div className="max-w-5xl mx-auto pb-12">
      <div className="inline-flex items-center gap-2 text-amber-700 bg-amber-100/70 rounded-full px-3 py-1 text-xs font-semibold"><Award size={14} /> Discover & rank</div>
      <h1 className="text-3xl font-bold tracking-tight mt-4">Turn great moments into a countdown.</h1>
      <p className="text-sm text-zinc-500 mt-2 max-w-xl">Real clips. Bold title. A numbered list that reveals each moment. Build a #5 → #1 reel in the style of your reference.</p>
      <div className="grid lg:grid-cols-[1fr_270px] gap-8 mt-8 items-start">
        <form onSubmit={submit} className="space-y-5">
          <section className="bg-white border border-zinc-200 rounded-2xl p-6 space-y-5 shadow-sm">
            <div><label htmlFor="ranking-topic" className="block text-xs font-semibold text-zinc-500 mb-2">WHAT ARE WE RANKING?</label>
              <input id="ranking-topic" value={topic} maxLength={180} onChange={e => setTopic(e.target.value)} placeholder="e.g. Ranking funny cat moments" className="w-full text-lg font-medium focus:outline-none placeholder:text-zinc-300" required /></div>
            <div className="flex gap-2 flex-wrap">
              {['Funny cat moments', 'Parkour fails', 'Football saves'].map(example => <button key={example} type="button" onClick={() => setTopic(example)} className="text-xs px-3 py-1.5 rounded-full border border-zinc-200 hover:bg-zinc-50">{example}</button>)}
            </div>
            <div className="border-t border-zinc-100 pt-4">
              <p className="text-xs font-semibold text-zinc-500 mb-3">CHOOSE YOUR FOOTAGE</p>
              <div className="grid grid-cols-3 gap-2">
                {([{ id: 'discover', text: 'Find videos', icon: Search }, { id: 'links', text: 'Source links', icon: Link }, { id: 'upload', text: 'Upload clips', icon: Upload }] as const).map(mode => <button type="button" key={mode.id} onClick={() => setSourceMode(mode.id)} className={`flex items-center justify-center gap-2 py-3 rounded-xl border text-xs font-medium ${sourceMode === mode.id ? 'bg-zinc-900 text-white border-zinc-900' : 'border-zinc-200 hover:bg-zinc-50'}`}><mode.icon size={14} />{mode.text}</button>)}
              </div>
              {sourceMode === 'discover' && <p className="text-xs text-zinc-500 leading-relaxed mt-3">Searches public videos about your topic, downloads usable sources, and compares their strongest moments. If sources are unavailable, the job shows a reason and lets you retry with links or uploads.</p>}
              {sourceMode === 'links' && <div className="mt-3"><textarea aria-label="Source video links" value={links} onChange={e => setLinks(e.target.value)} placeholder="One public video URL per line" rows={5} className="w-full p-3 bg-zinc-50 rounded-xl border border-zinc-200 text-xs focus:outline-none focus:border-zinc-500" /><p className="text-xs text-zinc-500 mt-2">{sourceUrls.length} sources · at least {count} distinct videos needed</p></div>}
              {sourceMode === 'upload' && <label className="flex flex-col items-center gap-2 border-2 border-dashed border-zinc-200 rounded-xl p-6 mt-3 cursor-pointer hover:bg-zinc-50"><Upload size={22} className="text-zinc-400" /><span className="text-xs font-medium">{files.length ? `${files.length} videos selected` : `Choose ${count} or more videos`}</span><span className="text-[11px] text-zinc-400">MP4, MOV, WebM · up to 2 GB each</span><input aria-label="Ranking source files" type="file" multiple accept="video/*" className="sr-only" onChange={e => setFiles(Array.from(e.target.files || []))} />{files.length > 0 && <span className="text-[11px] text-zinc-500 max-w-full break-all">{files.map(f => f.name).join(', ')}</span>}</label>}
            </div>
          </section>
          <section className="bg-white border border-zinc-200 rounded-2xl p-6 grid grid-cols-2 gap-4 shadow-sm">
            <div><label htmlFor="ranking-count" className="block text-xs text-zinc-600 mb-2">Number of ranks</label><select id="ranking-count" value={count} onChange={e => setCount(Number(e.target.value))} className="creator-select">{Array.from({ length: 8 }, (_, i) => i + 3).map(n => <option key={n} value={n}>Top {n}</option>)}</select></div>
            <div><label htmlFor="ranking-seconds" className="block text-xs text-zinc-600 mb-2">Seconds per moment</label><select id="ranking-seconds" value={seconds} onChange={e => setSeconds(Number(e.target.value))} className="creator-select"><option value={5}>5s · quick cuts</option><option value={7}>7s · balanced</option><option value={10}>10s · more context</option></select></div>
            <div><label htmlFor="ranking-layout" className="block text-xs text-zinc-600 mb-2">Footage framing</label><select id="ranking-layout" value={layout} onChange={e => setLayout(e.target.value as 'fill' | 'fit')} className="creator-select"><option value="fill">Fill frame · center crop</option><option value="fit">Keep full frame · blur background</option></select></div>
            <div><label htmlFor="ranking-provider" className="block text-xs text-zinc-600 mb-2">Analysis engine</label><select id="ranking-provider" value={provider} onChange={e => setProvider(e.target.value)} className="creator-select"><option value="auto">Auto · AI or visual metrics</option><option value="gemini">Gemini · frame analysis</option><option value="openai">OpenAI · frame analysis</option><option value="local">Local · vision model required</option></select></div>
            <label className="col-span-2 flex items-center gap-3 pt-2 border-t border-zinc-100 cursor-pointer"><input type="checkbox" checked={narration} onChange={e => setNarration(e.target.checked)} className="accent-zinc-900" /><Volume2 size={16} className="text-zinc-400" /><span className="text-xs"><span className="font-medium">Add brief countdown narration</span><span className="block text-zinc-400 mt-1">Off by default, keeping the original clip audio.</span></span></label>
          </section>
          {error && <div role="alert" className="bg-red-50 border border-red-200 text-red-700 rounded-xl p-4 text-sm">{error}</div>}
          <button disabled={loading || !topic.trim()} className="w-full flex justify-center items-center gap-2 bg-zinc-900 text-white rounded-xl py-3.5 text-sm font-semibold disabled:opacity-40 hover:bg-zinc-800">{loading ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}{loading ? 'Starting your countdown…' : 'Find, rank & create reel'}</button>
        </form>
        <aside className="hidden lg:block sticky top-6">
          <div className="bg-zinc-950 rounded-[28px] p-3 border border-zinc-800 shadow-xl">
            <div className="aspect-[9/16] rounded-2xl overflow-hidden bg-[#27272a] relative">
              <div className="bg-black text-white text-center pt-7 pb-3 px-3 text-[18px] font-black leading-tight uppercase tracking-tighter">RANKING <span className="text-yellow-300">{topic.replace(/^(top|best)\s+\d+\s*/i, '') || 'YOUR BEST MOMENTS'}</span></div>
              <div className="absolute inset-x-0 top-32 bottom-16 flex flex-col justify-center items-center text-zinc-500 gap-3"><Film size={40} strokeWidth={1} /><span className="text-[10px] uppercase tracking-widest">Your source footage</span></div>
              <div className="relative pt-8 pl-3 flex flex-col gap-5">{Array.from({ length: Math.min(count, 7) }, (_, i) => <div key={i} className="text-2xl font-black" style={{ color: ['#eaff4b', '#ffffff', '#ff5151', '#7aff69', '#f9a1d9'][i % 5], textShadow: '2px 2px black' }}>{i + 1}. {i === count - 1 && <span className="text-xs text-white">First reveal</span>}</div>)}</div>
              <div className="absolute bottom-0 inset-x-0 bg-black py-3 text-center text-[10px] text-zinc-400">#{count} → #1 · Original audio</div>
            </div>
          </div>
          <p className="text-center text-xs text-zinc-400 mt-4">Layout preview · 720 × 1280</p>
          <a href="https://www.youtube.com/shorts/4z8Hi_uQOkE" target="_blank" rel="noreferrer" className="block text-center text-xs text-zinc-500 underline mt-2">View your style reference</a>
        </aside>
      </div>
    </div>
  </div>;
};
