import React, { useEffect, useState } from 'react';
import { Upload } from 'lucide-react';
import { configureYouTube, connectYouTube, disconnectYouTube, getYouTubeConnection } from '../api';
import { YouTubeConnection } from '../types';

export const YouTubeSettings: React.FC = () => {
  const [connection, setConnection] = useState<YouTubeConnection | null>(null);
  const [document, setDocument] = useState<unknown>();
  const [fileName, setFileName] = useState('');
  const [privacy, setPrivacy] = useState('private');
  const [kids, setKids] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const refresh = () => getYouTubeConnection().then(value => { setConnection(value); return value; });
  useEffect(() => {
    refresh().then(value => { setPrivacy(value.default_privacy); setKids(value.default_made_for_kids); }).catch(e => setError(e.message));
    const timer = setInterval(() => { refresh().catch(() => {}); }, 4000);
    return () => clearInterval(timer);
  }, []);

  const perform = async (action: 'save' | 'connect' | 'disconnect') => {
    setBusy(true); setError(''); setMessage('');
    // Open synchronously only for initial Google sign-in, never for uploading.
    const signIn = action === 'connect' ? window.open('about:blank', '_blank') : null;
    if (signIn) signIn.opener = null;
    try {
      if (action === 'disconnect') {
        setConnection(await disconnectYouTube()); setMessage('Channel disconnected from this app.');
      } else {
        const updated = await configureYouTube({ client_document: document, privacy, made_for_kids: kids });
        setConnection(updated); setDocument(undefined); setFileName('');
        if (action === 'connect') {
          if (!signIn) throw new Error('Allow the Google sign-in tab, then click Connect YouTube again.');
          const result = await connectYouTube();
          signIn.location.href = result.authorization_url;
          setMessage('Finish Google sign-in in the new tab. Future uploads stay inside this app.');
        } else setMessage('YouTube upload defaults saved.');
      }
    } catch (e: any) { signIn?.close(); setError(e.message); }
    finally { setBusy(false); }
  };

  return <section className="bg-white rounded-2xl border border-zinc-200 shadow-sm p-6 space-y-4">
    <div className="flex items-center gap-2 border-b border-zinc-100 pb-3"><Upload className="w-5 h-5 text-red-600" /><h3 className="text-sm font-semibold">YouTube direct upload</h3></div>
    <p className="text-xs text-zinc-500">Connect once, then upload each finished reel with one click. Video transfer and progress stay in the app; uploads never redirect to YouTube Studio.</p>
    <p className="text-xs text-zinc-500">Uploads start private. ClipRank monitors processing and rejections automatically. Confirm the copyright verdict from Studio once; ClipRank then applies your chosen visibility. The standard YouTube API does not expose that verdict.</p>
    <p className="text-xs text-zinc-500">Descriptions: 13 dot lines, creator credits without links, then a summary of what happens in the Short. Each upload includes 32 relevant YouTube keyword tags.</p>
    <p className={`text-xs font-medium ${connection?.connected ? 'text-emerald-700' : 'text-zinc-600'}`}>{connection?.connected ? `Connected channel: ${connection.channel_title}` : connection?.configured ? 'OAuth client ready. Connect your channel below.' : 'Add a Desktop app OAuth client JSON to connect.'}</p>
    <details className="text-xs text-zinc-600"><summary className="cursor-pointer font-medium">One-time Google setup</summary><ol className="list-decimal ml-4 space-y-2 mt-3">
      <li>In <a href="https://console.cloud.google.com/apis/library/youtube.googleapis.com" target="_blank" rel="noreferrer" className="underline">Google Cloud</a>, enable YouTube Data API v3.</li>
      <li>Configure the OAuth consent screen. If the app is in Testing, add your Google account as a test user.</li>
      <li>Create an OAuth client with application type <strong>Desktop app</strong>, download its JSON, and choose it below.</li>
      <li>Click Connect YouTube and approve upload and channel access on Google’s sign-in screen.</li>
    </ol><p className="mt-3">Google can restrict uploads from unverified API projects to Private. Public uploads may require a <a href="https://developers.google.com/youtube/v3/docs/videos/insert" target="_blank" rel="noreferrer" className="underline">YouTube API audit</a>.</p></details>
    <label className="block text-xs font-medium">Google Desktop OAuth client JSON<input type="file" accept="application/json,.json" className="block mt-2 text-xs w-full" onChange={async e => {
      const file = e.target.files?.[0]; e.target.value = ''; if (!file) return;
      setError(''); setDocument(undefined); setFileName('');
      if (file.size > 65536) { setError('Choose a Google OAuth client JSON smaller than 64 KB.'); return; }
      try { setDocument(JSON.parse(await file.text())); setFileName(file.name); } catch { setError('This file is not valid JSON.'); }
    }} /></label>
    {fileName && <p className="text-xs text-zinc-500">Selected: {fileName}</p>}
    <div className="grid sm:grid-cols-2 gap-4">
      <label className="text-xs text-zinc-600">Default visibility<select className="creator-select mt-2" value={privacy} onChange={e => setPrivacy(e.target.value)}><option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label>
      <label className="text-xs text-zinc-600">Default audience<select className="creator-select mt-2" value={String(kids)} onChange={e => setKids(e.target.value === 'true')}><option value="false">Not made for kids</option><option value="true">Made for kids</option></select></label>
    </div>
    <p className="text-[11px] text-zinc-500">Credentials stay in local app data and are excluded from Git. Disconnect removes the saved channel tokens from this app.</p>
    <div className="flex flex-wrap gap-3">
      <button type="button" disabled={busy} onClick={() => perform('connect')} className="bg-zinc-900 text-white text-xs font-semibold px-4 py-2.5 rounded-xl disabled:opacity-50">{connection?.connected ? 'Reconnect YouTube' : 'Connect YouTube'}</button>
      <button type="button" disabled={busy} onClick={() => perform('save')} className="text-xs font-semibold border border-zinc-200 px-4 py-2.5 rounded-xl disabled:opacity-50">Save upload defaults</button>
      {connection?.connected && <button type="button" disabled={busy} onClick={() => perform('disconnect')} className="text-xs text-red-600 underline disabled:opacity-50">Disconnect</button>}
    </div>
    {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
    {message && <p role="status" className="text-xs text-zinc-600">{message}</p>}
  </section>;
};
