import React, { useEffect, useState } from 'react';
import { studioRequest } from '../api';

const LABELS: Record<string, { name: string; text: string }> = {
  relaxed: { name: 'Relaxed', text: 'Finds the most footage. The AI needs to be fairly sure (65%), skips extra re-reviews, and tolerates minor style issues.' },
  balanced: { name: 'Balanced', text: 'A middle path: 75% certainty, one review pass fewer than Strict.' },
  strict: { name: 'Strict', text: 'The original bar: 85% certainty, extra independent re-reviews, every style check must pass. Finds the least footage.' },
};

/** How demanding the AI's judgment is for Viral Discovery and for production. File checks never relax. */
export const StrictnessControl: React.FC<{ onChange?: () => void }> = ({ onChange }) => {
  const [level, setLevel] = useState<string>('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [whole, setWhole] = useState(true);
  useEffect(() => { studioRequest('/strictness').then(r => { setLevel(r.production === r.discovery ? r.production : r.discovery); setWhole(r.use_whole_video !== false); }).catch(e => setError(e.message)); }, []);
  const toggleWhole = async (enabled: boolean) => {
    setBusy(true); setError('');
    try { await studioRequest('/whole-video', { enabled }); setWhole(enabled); onChange?.(); }
    catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };
  const choose = async (next: string) => {
    setBusy(true); setError('');
    try { await studioRequest('/strictness', { level: next, scope: 'both' }); setLevel(next); onChange?.(); }
    catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };
  return <section aria-label="AI strictness" className="rounded-2xl border border-zinc-200 bg-white p-4 space-y-2">
    <div className="flex items-center justify-between gap-3 flex-wrap">
      <p className="text-sm font-semibold text-zinc-900">AI strictness <span className="font-normal text-zinc-500">· Discovery and production</span></p>
      <div className="flex gap-1.5" role="group" aria-label="Strictness level">
        {Object.entries(LABELS).map(([id, item]) => <button key={id} type="button" disabled={busy} aria-pressed={level === id} onClick={() => choose(id)}
          className={`text-xs px-3 py-1.5 rounded-full border ${level === id ? 'bg-zinc-900 text-white border-zinc-900' : 'border-zinc-200 text-zinc-600 hover:bg-zinc-50'}`}>{item.name}</button>)}
      </div>
    </div>
    {level && <p className="text-xs text-zinc-500">{LABELS[level]?.text}</p>}
    <label className="flex items-start gap-2 text-xs text-zinc-700 cursor-pointer">
      <input type="checkbox" aria-label="Use the whole video" checked={whole} disabled={busy} onChange={e => toggleWhole(e.target.checked)} className="accent-zinc-900 mt-0.5" />
      <span><b>Use the whole video</b> for Shorts made from a discovered moment, instead of only the ~10 s highlight found inside it. Videos longer than 3 minutes are cut to the first 179 s.</span>
    </label>
    <p className="text-[11px] text-zinc-400">Always enforced at every level: a valid 1080×1920 video file, no black or frozen sections, clean audio, narration present, and no graphic injury.</p>
    {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
  </section>;
};
