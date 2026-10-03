import React, { useEffect, useState } from 'react';
import { Award, ArrowRight, Loader2, Search, Link, Upload, Volume2, Film } from 'lucide-react';
import { createRankingProject, createRankingUpload, getSettings, getRankingTopics, analyzeTopic, TopicSuggestion, TopicAnalysis } from '../api';
import { VoicePicker } from '../components/VoicePicker';
import { NARRATION_VOICES } from '../voices';
import { AIStatus } from '../types';

type SourceMode = 'discover' | 'links' | 'upload';

export const RankingPage: React.FC<{ onStartJob: (jobId: string, projectId: string) => void; aiStatus: AIStatus | null; onOpenSettings: () => void }> = ({ onStartJob, aiStatus, onOpenSettings }) => {
  const [topic, setTopic] = useState('');
  const [count, setCount] = useState(5);
  const [provider, setProvider] = useState('auto');
  const [sourceMode, setSourceMode] = useState<SourceMode>('discover');
  const [links, setLinks] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const narration = true;
  const [voice, setVoice] = useState('pocket:alba');
  const [platforms, setPlatforms] = useState(['youtube', 'reddit', 'dailymotion']);
  const layout = 'smart' as const;
  const [seconds, setSeconds] = useState(7);
  const [variants, setVariants] = useState<1 | 2>(1);
  const [ccOnly, setCcOnly] = useState(false);
  const [topicFilter, setTopicFilter] = useState<'all' | 'broad' | 'narrow'>('broad');
  const [suggestions, setSuggestions] = useState<TopicSuggestion[]>([]);
  const [analysis, setAnalysis] = useState<TopicAnalysis | null>(null);
  const [topicsError, setTopicsError] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const sourceUrls = [...new Set(links.split(/\n/).map(s => s.trim()).filter(Boolean))];
  const visionReady = !!aiStatus && (provider === 'auto' ? aiStatus.ranking_ready :
    provider === 'gemini' ? aiStatus.gemini_configured : provider === 'local' ? aiStatus.local_available :
    provider === 'groq' ? !!aiStatus.groq_configured : provider === 'nvidia' ? !!aiStatus.nvidia_configured : aiStatus.openai_configured);

  useEffect(() => {
    getSettings().then(settings => {
      if (NARRATION_VOICES.some(item => item.id === settings.default_voice)) setVoice(settings.default_voice);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    getRankingTopics(topicFilter).then(rows => { setSuggestions(rows); setTopicsError(''); })
      .catch(e => setTopicsError(e.message));
  }, [topicFilter]);

  useEffect(() => {
    if (!topic.trim()) { setAnalysis(null); return; }
    const timer = setTimeout(() => analyzeTopic(topic.trim()).then(setAnalysis).catch(() => setAnalysis(null)), 300);
    return () => clearTimeout(timer);
  }, [topic]);

  useEffect(() => {
    const match = topic.match(/\b(?:top|best)\s*(\d{1,2})\b/i);
    if (match && Number(match[1]) >= 3 && Number(match[1]) <= 10) setCount(Number(match[1]));
  }, [topic]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    if (!visionReady) return setError('Connect a vision model in Settings to verify the topic and actions first.');
    if (sourceMode === 'discover' && !platforms.length) return setError('Choose at least one site to search.');
    if (sourceMode === 'links' && sourceUrls.length < count * variants) return setError(`Add at least ${count * variants} distinct unused video links for Top ${count}.`);
    if (sourceMode === 'upload' && files.length < count * variants) return setError(`Select at least ${count * variants} different unused source videos.`);
    setLoading(true);
    try {
      let response;
      if (sourceMode === 'upload') {
        const data = new FormData();
        files.forEach(file => data.append('video_files', file));
        data.append('topic', topic.trim()); data.append('count', String(count));
        data.append('ai_provider', provider); data.append('narration', String(narration));
        data.append('layout', layout); data.append('segment_duration', String(seconds));
        data.append('voice', voice); data.append('variants', String(variants));
        response = await createRankingUpload(data);
      } else {
        response = await createRankingProject({ topic: topic.trim(), count, ai_provider: provider,
          source_urls: sourceMode === 'links' ? sourceUrls : [], narration, voice,
          source_platforms: platforms.length ? platforms : ['youtube'], layout, segment_duration: seconds, variants, cc_only: ccOnly });
      }
      onStartJob(response.job_id, response.project_id);
    } catch (err: any) { setError(err.message || 'Could not start ranking video.'); }
    finally { setLoading(false); }
  };

  return <div className="flex-1 overflow-y-auto p-6 lg:p-10 bg-[#FAF9F6]">
    <div className="max-w-5xl mx-auto pb-12">
      <div className="inline-flex items-center gap-2 text-amber-700 bg-amber-100/70 rounded-full px-3 py-1 text-xs font-semibold"><Award size={14} /> Discover & rank</div>
      <h1 className="text-3xl font-bold tracking-tight mt-4">One topic. {variants === 1 ? 'One finished Short.' : 'Two finished Shorts.'}</h1>
      <p className="text-sm text-zinc-500 mt-2 max-w-xl">{variants === 1 ? 'Escalates from #' + count + ' to #1 with original narration, timed captions and finished audio at 1080p.' : 'Short A brings fast entertainment. Short B builds suspense. Both use different footage.'} Each exceeds 10 seconds and preserves the complete payoff.</p>
      {!visionReady && <div className="mt-5 bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-900"><p className="font-semibold">Connect vision to match your topic</p><p className="text-xs mt-1 leading-relaxed">Add your Gemini key or connect an Ollama vision model. Every cut must show your requested subject, action and outcome before it can be ranked.</p><button type="button" onClick={onOpenSettings} className="mt-3 text-xs font-semibold underline">Open vision settings</button></div>}
      <div className="grid lg:grid-cols-[1fr_270px] gap-8 mt-8 items-start">
        <form onSubmit={submit} className="space-y-5">
          <section className="bg-white border border-zinc-200 rounded-2xl p-6 space-y-5 shadow-sm">
            <div><label htmlFor="ranking-topic" className="block text-xs font-semibold text-zinc-500 mb-2">WHAT ARE WE RANKING?</label>
              <input id="ranking-topic" value={topic} maxLength={180} onChange={e => setTopic(e.target.value)} placeholder="e.g. Ranking funny cat moments" className="w-full text-lg font-medium focus:outline-none placeholder:text-zinc-300" required /></div>
            {analysis && analysis.breadth === 'narrow' && <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
              <p className="font-semibold">Narrow topic: fewer clips will qualify</p>
              <p className="mt-1">{analysis.reasons.join(' ')}</p>
              {analysis.broader && <button type="button" onClick={() => setTopic(analysis.broader as string)} className="mt-2 font-semibold underline">Use the broader topic: {analysis.broader}</button>}
            </div>}
            {analysis && analysis.breadth === 'broad' && <p className="text-[11px] text-emerald-700">Broad topic: any impressive visible moment of this subject can qualify, so more clips pass verification.</p>}
            <div>
              <div className="flex items-center gap-2 mb-2" role="group" aria-label="Topic breadth filter">
                <span className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">Suggestions</span>
                {([['broad', 'Broader'], ['narrow', 'Narrower'], ['all', 'All']] as const).map(([id, label]) =>
                  <button key={id} type="button" aria-pressed={topicFilter === id} onClick={() => setTopicFilter(id)} className={`text-[11px] px-2.5 py-1 rounded-full border ${topicFilter === id ? 'bg-zinc-900 text-white border-zinc-900' : 'border-zinc-200 text-zinc-600 hover:bg-zinc-50'}`}>{label}</button>)}
              </div>
              {topicsError && <p role="alert" className="text-xs text-red-600">{topicsError}</p>}
              <div className="flex gap-2 flex-wrap">
                {suggestions.map(item => <button key={item.topic} type="button" title={item.breadth === 'broad' ? 'Broad: more clips qualify' : 'Narrow: needs a specific outcome'} onClick={() => setTopic(item.topic)} className="text-xs px-3 py-1.5 rounded-full border border-zinc-200 hover:bg-zinc-50">{item.topic}</button>)}
              </div>
            </div>
            <div className="border-t border-zinc-100 pt-4">
              <p className="text-xs font-semibold text-zinc-500 mb-3">CHOOSE YOUR FOOTAGE</p>
              <div className="grid grid-cols-3 gap-2">
                {([{ id: 'discover', text: 'Find videos', icon: Search }, { id: 'links', text: 'Source links', icon: Link }, { id: 'upload', text: 'Upload clips', icon: Upload }] as const).map(mode => <button type="button" key={mode.id} onClick={() => setSourceMode(mode.id)} className={`flex items-center justify-center gap-2 py-3 rounded-xl border text-xs font-medium ${sourceMode === mode.id ? 'bg-zinc-900 text-white border-zinc-900' : 'border-zinc-200 hover:bg-zinc-50'}`}><mode.icon size={14} />{mode.text}</button>)}
              </div>
              {sourceMode === 'discover' && <div className="mt-4 space-y-3"><p className="text-xs text-zinc-500">Search for individual clips on:</p><div className="flex flex-wrap gap-2">{[{ id: 'youtube', name: 'YouTube' }, { id: 'reddit', name: 'Reddit' }, { id: 'dailymotion', name: 'Dailymotion' }, { id: 'tiktok', name: 'TikTok' }, { id: 'instagram', name: 'Instagram' }, { id: 'vimeo', name: 'Vimeo' }].map(site => <label key={site.id} className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs cursor-pointer ${platforms.includes(site.id) ? 'border-zinc-600 bg-zinc-50' : 'border-zinc-200 text-zinc-500'}`}><input type="checkbox" checked={platforms.includes(site.id)} onChange={e => setPlatforms(current => e.target.checked ? [...current, site.id] : current.filter(p => p !== site.id))} className="accent-zinc-900" />{site.name}</label>)}</div><label className="flex items-start gap-2 text-xs text-zinc-700 bg-zinc-50 border border-zinc-200 rounded-lg p-3 cursor-pointer"><input type="checkbox" checked={ccOnly} onChange={e => setCcOnly(e.target.checked)} className="accent-zinc-900 mt-0.5" /><span><b>Only reusable (Creative Commons) footage</b> — searches YouTube's Creative Commons filter so the result can pass the rights gate with documented licences. Finds fewer clips.</span></label><p className="text-xs text-zinc-500 leading-relaxed">Only accessible public video pages can be downloaded. Some sites limit search or require sign-in; unavailable sources are skipped and reported.</p></div>}
              {sourceMode === 'links' && <div className="mt-3"><textarea aria-label="Source video links" value={links} onChange={e => setLinks(e.target.value)} placeholder="One public video URL per line · YouTube, Reddit, Dailymotion, TikTok, Instagram, Vimeo…" rows={5} className="w-full p-3 bg-zinc-50 rounded-xl border border-zinc-200 text-xs focus:outline-none focus:border-zinc-500" /><p className="text-xs text-zinc-500 mt-2">{sourceUrls.length} sources · at least {count * variants} different unused videos needed</p></div>}
              {sourceMode === 'upload' && <label className="flex flex-col items-center gap-2 border-2 border-dashed border-zinc-200 rounded-xl p-6 mt-3 cursor-pointer hover:bg-zinc-50"><Upload size={22} className="text-zinc-400" /><span className="text-xs font-medium">{files.length ? `${files.length} videos selected` : `Choose ${count * variants} or more unused videos`}</span><span className="text-[11px] text-zinc-400">MP4, MOV, WebM · up to 2 GB each</span><input aria-label="Ranking source files" type="file" multiple accept="video/*" className="sr-only" onChange={e => setFiles(Array.from(e.target.files || []))} />{files.length > 0 && <span className="text-[11px] text-zinc-500 max-w-full break-all">{files.map(f => f.name).join(', ')}</span>}</label>}
              <p className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-100 rounded-lg p-3 mt-4">Topic check required. Each selected cut must visibly match your subject, action and outcome. Existing rankings are excluded, and the clip label and commentary are checked against the final framing.</p>
            </div>
          </section>
          <section className="bg-white border border-zinc-200 rounded-2xl p-6 grid grid-cols-2 gap-4 shadow-sm">
            <div><label htmlFor="ranking-count" className="block text-xs text-zinc-600 mb-2">Number of ranks</label><select id="ranking-count" value={count} onChange={e => setCount(Number(e.target.value))} className="creator-select">{Array.from({ length: 8 }, (_, i) => i + 3).map(n => <option key={n} value={n}>Top {n}</option>)}</select></div>
            <div><label htmlFor="ranking-seconds" className="block text-xs text-zinc-600 mb-2">Target seconds per moment</label><select id="ranking-seconds" value={seconds} onChange={e => setSeconds(Number(e.target.value))} className="creator-select"><option value={5}>5s · quick cuts</option><option value={7}>7s · balanced</option><option value={10}>10s · more context</option></select></div>
            <div><label htmlFor="ranking-variants" className="block text-xs text-zinc-600 mb-2">Versions</label><select id="ranking-variants" value={variants} onChange={e => setVariants(Number(e.target.value) as 1 | 2)} className="creator-select"><option value={1}>One finished Short</option><option value={2}>Two distinct Shorts (A/B, needs {count * 2} sources)</option></select></div>
            <div><label className="block text-xs text-zinc-600 mb-2">Footage framing</label><div className="creator-select">Smart 9:16 · landscape cropped to follow the action</div></div>
            <div><label htmlFor="ranking-provider" className="block text-xs text-zinc-600 mb-2">Vision engine</label><select id="ranking-provider" value={provider} onChange={e => setProvider(e.target.value)} className="creator-select"><option value="auto">Auto · connected vision model</option><option value="gemini">Gemini · verify topic and action</option><option value="local">Local · Ollama vision model</option><option value="groq">Groq · verify topic and action</option><option value="nvidia">NVIDIA NIM · verify topic and action</option><option value="openai">OpenAI · verify topic and action</option></select></div>
            <div className="col-span-2 flex items-center gap-3 pt-2 border-t border-zinc-100"><Volume2 size={16} className="text-zinc-400" /><span className="text-xs"><span className="font-medium">Finished narration & captions included</span><span className="block text-zinc-400 mt-1">Natural voice, synchronized captions and smooth source-audio mixing.</span></span></div>
            {narration && <div className="col-span-2"><VoicePicker voice={voice} onChange={setVoice} /><p className="text-[11px] text-zinc-400 mt-2">{voice.startsWith('pocket:')?'Pocket TTS generates speech locally.':'Online speech requires internet.'} Commentary describes the verified action in the selected cut.</p></div>}
          </section>
          {error && <div role="alert" className="bg-red-50 border border-red-200 text-red-700 rounded-xl p-4 text-sm">{error}</div>}
          <p className="text-xs text-zinc-500">Only videos longer than 10 seconds with complete payoffs that pass visual, audio and caption checks appear for preview, download and direct YouTube upload.</p>
          <button disabled={loading || !topic.trim() || !visionReady} className="w-full flex justify-center items-center gap-2 bg-zinc-900 text-white rounded-xl py-3.5 text-sm font-semibold disabled:opacity-40 hover:bg-zinc-800">{loading ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}{loading ? 'Starting production…' : variants === 1 ? 'Produce the finished Short' : 'Produce two finished Shorts'}</button>
        </form>
        <aside className="hidden lg:block sticky top-6">
          <div className="bg-zinc-950 rounded-[28px] p-3 border border-zinc-800 shadow-xl">
            <div className="aspect-[9/16] rounded-2xl overflow-hidden bg-[#27272a] relative">
              <div className="bg-black text-white text-center pt-7 pb-3 px-3 text-[18px] font-black leading-tight uppercase tracking-tighter">RANKING <span className="text-yellow-300">{topic.replace(/^(top|best)\s+\d+\s*/i, '') || 'YOUR BEST MOMENTS'}</span></div>
              <div className="absolute inset-x-0 top-32 bottom-16 flex flex-col justify-center items-center text-zinc-500 gap-3"><Film size={40} strokeWidth={1} /><span className="text-[10px] uppercase tracking-widest">Your source footage</span></div>
              <div className="absolute top-20 left-4 bg-yellow-300 text-zinc-950 rounded-lg px-3 py-1 text-xl font-black">#{count}</div>
              <div className="absolute bottom-20 left-5 right-7 text-center text-white font-black text-lg leading-tight">WATCH THE LANDING</div>
              <div className="absolute bottom-0 inset-x-0 bg-black py-3 text-center text-[10px] text-zinc-400">#{count} → #1 · {narration ? 'Neural commentary' : 'Original audio'}</div>
            </div>
          </div>
          <p className="text-center text-xs text-zinc-400 mt-4">{variants === 1 ? 'One edit' : 'Two edits'} · 1080 × 1920 · 48 kHz audio</p>
          <a href="https://www.youtube.com/shorts/4z8Hi_uQOkE" target="_blank" rel="noreferrer" className="block text-center text-xs text-zinc-500 underline mt-2">View your style reference</a>
        </aside>
      </div>
    </div>
  </div>;
};
