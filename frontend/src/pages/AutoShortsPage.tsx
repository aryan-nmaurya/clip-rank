import React, { useEffect, useState } from 'react';
import { Wand2, Loader2, Download, ExternalLink } from 'lucide-react';
import { startAutoShorts, getAutoShorts, cancelAutoShorts } from '../api';
import { VoicePicker } from '../components/VoicePicker';
import { YouTubeUploadButton } from '../components/YouTubeUploadButton';

const SITES = [{ id: 'youtube', name: 'YouTube' }, { id: 'dailymotion', name: 'Dailymotion' }, { id: 'reddit', name: 'Reddit' }, { id: 'vimeo', name: 'Vimeo' }];
const NICHES = [{ id: 'extreme', name: 'Extreme & unbelievable' }, { id: 'funny', name: 'Funny fails' }, { id: 'skills', name: 'Skills & tricks' }, { id: 'general', name: 'Anything promising' }];

export const AutoShortsPage: React.FC = () => {
  const [mode, setMode] = useState<'viral' | 'emotional' | 'sad_visual'>('viral');
  const [count, setCount] = useState(3);
  const [platforms, setPlatforms] = useState(['youtube', 'dailymotion', 'reddit']);
  const [niche, setNiche] = useState('extreme');
  const [topic, setTopic] = useState('');
  const [voice, setVoice] = useState('pocket:alba');
  const [run, setRun] = useState<any>(null);
  const [error, setError] = useState('');
  const running = run?.status === 'running';

  useEffect(() => { getAutoShorts().then(setRun).catch(() => {}); }, []);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => { getAutoShorts().then(setRun).catch(e => setError(e.message)); }, 2000);
    return () => clearInterval(timer);
  }, [running]);

  const begin = async () => {
    setError('');
    try { setRun(await startAutoShorts({ count, platforms, niche, topic: topic.trim() || undefined, voice, mode })); }
    catch (e: any) { setError(e.message); }
  };
  const items: any[] = run?.items || [];

  return <div className="flex-1 overflow-y-auto p-6 lg:p-10 bg-[#FAF9F6]"><div className="max-w-4xl mx-auto pb-16 space-y-6">
    <div><div className="inline-flex items-center gap-2 text-fuchsia-800 bg-fuchsia-100/70 rounded-full px-3 py-1 text-xs font-semibold"><Wand2 size={14} /> One click</div>
      <h1 className="text-3xl font-bold tracking-tight mt-4">Auto Shorts</h1>
      <p className="text-sm text-zinc-500 mt-2 max-w-2xl">ClipRank searches several platforms for videos with real momentum, picks the most promising, then edits each into a phone-sized Short with a voice-over, captions and mixed audio. Finished Shorts are ready to post to YouTube.</p></div>

    <div className="grid sm:grid-cols-2 gap-3" role="radiogroup" aria-label="What kind of Short">
      {([['viral', 'Viral clip + commentary', 'Finds a clip with real momentum and adds an original voice-over, captions and a phone-sized edit.'],
         ['emotional', 'Emotional story + heading + sad music', 'Finds a deeply moving real moment. Keeps its original sound, adds a headline over the whole video, soft sad background music and captions.'],
         ['sad_visual', 'Sad visuals only + heading + sad music', 'Finds a sad, moving picture story. Removes the original sound and adds only a headline over the whole video and soft sad background music. No voice-over, no captions.']] as const).map(([id, title, text]) =>
        <button key={id} type="button" role="radio" aria-checked={mode === id} disabled={running} onClick={() => setMode(id)}
          className={`text-left rounded-2xl border p-4 transition-all ${mode === id ? 'border-zinc-900 ring-1 ring-zinc-900 bg-white' : 'border-zinc-200 bg-white hover:border-zinc-300'} disabled:opacity-60`}>
          <p className="text-sm font-semibold text-zinc-900">{title}</p><p className="text-xs text-zinc-500 mt-1 leading-relaxed">{text}</p></button>)}
    </div>

    <section className="bg-white border border-zinc-200 rounded-2xl p-5 space-y-4 shadow-sm">
      <div className="grid sm:grid-cols-2 gap-4">
        <label className="text-xs text-zinc-600">How many Shorts
          <select aria-label="Number of Shorts" className="creator-select mt-1" value={count} disabled={running} onChange={e => setCount(Number(e.target.value))}>{[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
        {mode === 'viral' && <label className="text-xs text-zinc-600">Kind of videos
          <select aria-label="Kind of videos" className="creator-select mt-1" value={niche} disabled={running} onChange={e => setNiche(e.target.value)}>{NICHES.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}</select></label>}
        <label className="text-xs text-zinc-600 sm:col-span-2">Or a specific topic (optional)
          <input aria-label="Topic" className="creator-select mt-1" value={topic} disabled={running} maxLength={120} onChange={e => setTopic(e.target.value)} placeholder="e.g. skateboard last-second recoveries" /></label>
      </div>
      <div><p className="text-xs text-zinc-600 mb-2">Search on</p>
        <div className="flex flex-wrap gap-2">{SITES.map(site => <label key={site.id} className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs cursor-pointer ${platforms.includes(site.id) ? 'border-zinc-600 bg-zinc-50' : 'border-zinc-200 text-zinc-500'}`}>
          <input type="checkbox" disabled={running} checked={platforms.includes(site.id)} onChange={e => setPlatforms(c => e.target.checked ? [...c, site.id] : c.filter(p => p !== site.id))} className="accent-zinc-900" />{site.name}</label>)}</div></div>
      {mode === 'viral' && <VoicePicker voice={voice} onChange={setVoice} />}
      <p className="text-[11px] text-zinc-400">Each video is used whole (up to 3 minutes), cropped to follow the action, {mode === 'viral' ? 'with an original voice-over line, word-timed captions and balanced audio' : mode === 'sad_visual' ? 'with its original sound removed, a headline over the whole video and original sad background music only (no voice-over, no captions). Imported licensed sad tracks are used first, otherwise ClipRank synthesizes an original track' : 'with a headline over the whole video, original sad background music that ducks under any speech, and word-timed captions. If you have licensed sad tracks imported (Movie music library, mood "emotional"), those are used first; otherwise ClipRank synthesizes an original track, so there is nothing to clear'}. You manage reuse rights; the Quality-control switch in Settings decides how strictly results are checked.</p>
      {error && <p role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-xl p-3">{error}</p>}
      <div className="flex gap-3">
        <button onClick={begin} disabled={running || !platforms.length} className="flex-1 h-11 rounded-xl bg-zinc-900 text-white text-sm font-semibold flex items-center justify-center gap-2 disabled:opacity-40">
          {running ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}{running ? 'Working…' : `Find & make ${count} Short${count > 1 ? 's' : ''}`}</button>
        {running && <button onClick={() => cancelAutoShorts().then(setRun)} className="px-4 h-11 rounded-xl border border-zinc-300 text-sm font-medium">Cancel</button>}
      </div>
    </section>

    {run && run.status !== 'idle' && <section aria-label="Progress" className="space-y-3">
      <p role="status" className={`text-sm font-medium ${run.status === 'failed' ? 'text-red-700' : 'text-zinc-800'}`}>{run.message}</p>
      {run.warnings?.length > 0 && <p className="text-xs text-amber-700">{run.warnings.length} search feed(s) did not respond ({[...new Set(run.warnings.map((w: any) => w.platform))].join(', ')}).</p>}
      {items.map((item, index) => <article key={item.job_id} className="bg-white border border-zinc-200 rounded-2xl p-4 space-y-3">
        <div className="flex justify-between gap-3 items-start">
          <div className="min-w-0"><p className="text-[11px] uppercase tracking-wide text-zinc-400">#{index + 1} · {item.platform}{item.views ? ` · ${Number(item.views).toLocaleString()} views` : ''}{item.duration ? ` · ${item.duration}s` : ''}</p>
            <h2 className="font-semibold truncate">{item.title}</h2>
            <p className="text-xs text-zinc-500">{item.why}</p></div>
          <a href={item.url} target="_blank" rel="noreferrer" className="text-xs text-zinc-500 underline shrink-0">Source <ExternalLink size={11} className="inline" /></a>
        </div>
        {(item.status === 'QUEUED' || item.status === 'RUNNING') && <div><div className="h-1.5 rounded-full bg-zinc-100 overflow-hidden"><div className="h-full bg-zinc-900 transition-all" style={{ width: `${Math.max(4, item.progress || 0)}%` }} /></div><p className="text-xs text-zinc-500 mt-1">{item.stage}</p></div>}
        {item.status === 'FAILED' && <p role="alert" className="text-xs text-red-700 bg-red-50 rounded-lg p-2">Skipped: {item.error}{item.next_step ? ` — ${item.next_step}` : ''}. {run.status === 'running' ? 'Trying the next best video.' : ''}</p>}
        {item.status === 'DONE' && item.clips?.map((clip: any) => <div key={clip.id} className="grid sm:grid-cols-[180px_1fr] gap-4">
          <video controls preload="metadata" poster={clip.preview_path} src={clip.video_path} className="w-full rounded-xl bg-black aspect-[9/16]" />
          <div className="space-y-2 text-xs"><p className="font-semibold text-sm">{clip.title}</p><p className="text-zinc-500">{Math.round(clip.duration)}s · 1080×1920</p>
            <a href={`/api/clips/${clip.id}/download`} className="inline-flex items-center gap-1.5 h-9 px-3 rounded-xl bg-zinc-900 text-white font-medium"><Download size={13} />Download MP4</a>
            <YouTubeUploadButton clip={clip} /></div></div>)}
      </article>)}
    </section>}
  </div></div>;
};
