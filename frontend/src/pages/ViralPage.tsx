import React, { useState } from 'react';
import { Upload, Link, Flame, ArrowRight, ChevronDown, ChevronUp, Loader2, Sparkles } from 'lucide-react';
import { createViralProject } from '../api';

interface ViralPageProps {
  onStartJob: (jobId: string, projectId: string) => void;
}

export const ViralPage: React.FC<ViralPageProps> = ({ onStartJob }) => {
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [count, setCount] = useState(3);
  const [provider, setProvider] = useState('auto');
  const [seconds, setSeconds] = useState(25);
  const [layout, setLayout] = useState('fit');
  const [captions, setCaptions] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim() && !file) {
      setError('Please provide a video URL or upload a video file.');
      return;
    }

    setLoading(true);
    setError(null);

    const formData = new FormData();
    if (url.trim()) formData.append('video_url', url.trim());
    if (file) formData.append('video_file', file);
    formData.append('count', String(count));
    formData.append('ai_provider', provider);
    formData.append('target_duration', String(seconds));
    formData.append('layout', layout);
    formData.append('captions', String(captions));

    try {
      const res = await createViralProject(formData);
      onStartJob(res.job_id, res.project_id);
    } catch (err: any) {
      setError(err.message || 'Failed to start viral clips project.');
      setLoading(false);
    }
  };

  return (
    <div className="flex-1 h-screen overflow-y-auto flex flex-col items-center p-8 bg-[#FAF9F6]">
      <div className="max-w-2xl w-full flex flex-col gap-8 pb-12">
        {/* Header */}
        <div className="flex flex-col gap-2">
          <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-rose-50 text-rose-700 text-xs font-semibold uppercase tracking-wider w-fit">
            <Flame className="w-3.5 h-3.5" />
            <span>Workflow A</span>
          </div>
          <h1 className="text-3xl font-bold tracking-tight text-zinc-900">
            Create Viral Clips
          </h1>
          <p className="text-zinc-500 text-sm">
            Paste a video URL or upload a video. Keep your actual footage and audio. Find strong moments, add timed speech captions, and export vertical reels ranked by estimated retention potential.
          </p>
        </div>

        <div className="flex gap-5 text-xs text-zinc-500 bg-white border border-zinc-200 rounded-2xl p-4">
          <span>✂ Select complete moments</span><span>♫ Keep source audio</span><span>▥ Caption real speech</span>
        </div>
        {/* Input Card */}
        <form onSubmit={handleSubmit} className="flex flex-col gap-5">
          <div className="bg-white rounded-2xl border border-zinc-200/90 shadow-sm p-6 flex flex-col gap-4">
            <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
              Video URL or Upload
            </label>

            {/* URL Input */}
            <div className="relative">
              <Link className="w-4 h-4 text-zinc-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={url}
                onChange={(e) => {
                  setUrl(e.target.value);
                  if (e.target.value) setFile(null);
                }}
                placeholder="Paste video URL, e.g. https://youtube.com/watch?v=..."
                className="w-full h-11 pl-10 pr-4 rounded-xl bg-zinc-50 border border-zinc-200 text-sm font-medium text-zinc-900 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/10 focus:border-zinc-400"
              />
            </div>

            <div className="flex items-center gap-3">
              <div className="flex-1 h-px bg-zinc-100" />
              <span className="text-xs text-zinc-400 uppercase font-medium">or</span>
              <div className="flex-1 h-px bg-zinc-100" />
            </div>

            {/* File Upload Picker */}
            <label className="border-2 border-dashed border-zinc-200 hover:border-zinc-300 rounded-xl p-4 flex items-center justify-center gap-3 cursor-pointer bg-zinc-50/50 hover:bg-zinc-50 transition-colors">
              <Upload className="w-4 h-4 text-zinc-500" />
              <span className="text-xs font-medium text-zinc-700">
                {file ? file.name : 'Upload local video file (MP4, MOV)'}
              </span>
              <input
                type="file"
                accept="video/*"
                onChange={(e) => {
                  if (e.target.files?.[0]) {
                    setFile(e.target.files[0]);
                    setUrl('');
                  }
                }}
                className="hidden"
              />
            </label>

            {/* Quick Controls: Number of Clips & AI Provider */}
            <div className="grid grid-cols-2 gap-4 pt-2">
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-zinc-600">Number of clips</label>
                <select
                  value={count}
                  onChange={(e) => setCount(Number(e.target.value))}
                  className="h-10 px-3 rounded-xl bg-zinc-50 border border-zinc-200 text-xs text-zinc-800 focus:outline-none font-medium"
                >
                  <option value={3}>Up to 3 distinct clips</option>
                  <option value={1}>1 best clip</option>
                  <option value={5}>Up to 5 distinct clips</option>
                </select>
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-zinc-600">AI Provider</label>
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                  className="h-10 px-3 rounded-xl bg-zinc-50 border border-zinc-200 text-xs text-zinc-800 focus:outline-none font-medium"
                >
                  <option value="auto">Auto · AI or visual metrics</option>
                  <option value="gemini">Google AI Studio</option>
                  <option value="openai">OpenAI</option>
                  <option value="local">Local · vision model required</option>
                </select>
              </div>
            </div>
          </div>

          {error && (
            <div className="p-3 rounded-xl bg-red-50 text-red-700 text-xs font-medium border border-red-200">
              {error}
            </div>
          )}

          {/* Action Row */}
          <div className="flex items-center justify-between pt-1">
            <button
              type="button"
              onClick={() => setAdvancedOpen(!advancedOpen)}
              className="text-xs text-zinc-500 hover:text-zinc-900 font-medium flex items-center gap-1 transition-colors"
            >
              <span>Advanced</span>
              {advancedOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </button>

            <button
              type="submit"
              disabled={loading || (!url.trim() && !file)}
              className="h-11 px-6 rounded-xl bg-zinc-900 hover:bg-zinc-800 disabled:opacity-40 text-white font-medium text-sm flex items-center gap-2 transition-all shadow-sm active:scale-[0.98]"
            >
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Preparing pipeline...</span>
                </>
              ) : (
                <>
                  <span>Generate Viral Clips</span>
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </div>

          {/* Collapsible Advanced Section */}
          {advancedOpen && (
            <div className="p-5 rounded-2xl bg-white border border-zinc-200/90 shadow-sm grid grid-cols-2 gap-4 text-xs animate-in fade-in duration-150">
              <div className="flex flex-col gap-1.5">
                <span className="font-medium text-zinc-600">Target clip length</span>
                <select aria-label="Target clip length" value={seconds} onChange={e => setSeconds(Number(e.target.value))} className="creator-select"><option value={15}>15 seconds</option><option value={25}>25 seconds</option><option value={40}>40 seconds</option><option value={60}>60 seconds</option></select>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="font-medium text-zinc-600">Aspect ratio</span>
                <div className="p-2.5 rounded-lg border border-zinc-100 bg-zinc-50 text-zinc-500 font-medium">
                  9:16 Vertical (720 × 1280)
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="font-medium text-zinc-600">Speech captions</span>
                <label className="flex items-center gap-2 p-2.5 rounded-lg border border-zinc-100"><input type="checkbox" checked={captions} onChange={e => setCaptions(e.target.checked)} />Timed words · yellow highlight</label>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="font-medium text-zinc-600">Framing</span>
                <select aria-label="Clip framing" value={layout} onChange={e => setLayout(e.target.value)} className="creator-select"><option value="fit">Full footage · blurred background</option><option value="fill">Fill frame · center crop</option></select>
              </div>
            </div>
          )}
        </form>
      </div>
    </div>
  );
};
