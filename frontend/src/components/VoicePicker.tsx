import React, { useEffect, useRef, useState } from 'react';
import { Loader2, Play } from 'lucide-react';
import { getVoicePreview, getSpeechStatus } from '../api';
import { NARRATION_VOICES } from '../voices';

export const VoicePicker: React.FC<{ voice: string; onChange: (voice: string) => void }> = ({ voice, onChange }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [url, setUrl] = useState('');
  const [local, setLocal] = useState<any>(null);
  const audioUrl = useRef('');
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    getSpeechStatus().then(value=>{if(mounted.current)setLocal(value);}).catch(err=>{if(mounted.current)setError(err.message);});
    return () => { mounted.current = false; if (audioUrl.current) URL.revokeObjectURL(audioUrl.current); };
  }, []);
  const preview = async () => {
    setLoading(true); setError('');
    try {
      const audio = await getVoicePreview(voice);
      if (!mounted.current) return;
      if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
      audioUrl.current = URL.createObjectURL(audio);
      setUrl(audioUrl.current);
    } catch (err: any) { if (mounted.current) setError(err.message); }
    finally { if (mounted.current) setLoading(false); }
  };
  return <div className="space-y-2">
    <label className="block text-xs text-zinc-600">Narration voice
      <select value={voice} onChange={e => { onChange(e.target.value); setUrl(''); }} className="creator-select mt-2">
        {NARRATION_VOICES.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select>
    </label>
    {voice.startsWith('pocket:')&&<p className={`text-xs ${local?.ready?'text-emerald-700':'text-amber-800'}`}>{local?.message||'Checking Pocket TTS…'}</p>}
    <button type="button" disabled={loading||(voice.startsWith('pocket:')&&!local?.ready)} onClick={preview} className="inline-flex items-center gap-2 text-xs font-medium text-zinc-700 disabled:opacity-50">
      {loading ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}{loading ? 'Preparing voice…' : 'Preview voice'}
    </button>
    {voice.startsWith('pocket:')&&!local?.ready&&<button type="button" onClick={()=>getSpeechStatus().then(setLocal).catch(e=>setError(e.message))} className="ml-3 text-xs underline">Check local voice</button>}
    {url && <audio key={url} src={url} controls autoPlay className="w-full h-9" />}
    {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
  </div>;
};
