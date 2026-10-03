import React, { useCallback, useEffect, useState } from 'react';
import { ShieldCheck, ShieldAlert } from 'lucide-react';
import { getClipGates, recordRights } from '../api';
import { ClipGates } from '../types';

const BASES: Record<string, string> = {
  owned: 'I own this footage',
  licensed: 'I hold a commercial licence for it',
  written_permission: 'I have written permission from the rights holder',
  public_domain: 'It is in the public domain',
  creative_commons: 'It is under a commercial-use Creative Commons licence',
};

/** TECHNICAL / RIGHTS / ORIGINALITY: all three must pass before ClipRank will upload. */
export const PublishGates: React.FC<{ clipId: string; projectId: string; onChange?: (passed: boolean) => void }> = ({ clipId, projectId, onChange }) => {
  const [gates, setGates] = useState<ClipGates | null>(null);
  const [error, setError] = useState('');
  const [basis, setBasis] = useState('owned');
  const [note, setNote] = useState('');
  const [evidence, setEvidence] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => getClipGates(clipId).then(result => { setGates(result); onChange?.(result.passed); })
    .catch(e => setError(e.message)), [clipId, onChange]);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setBusy(true); setError('');
    try { await recordRights(projectId, { basis, note, evidence_url: evidence.trim() || undefined }); await load(); }
    catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };
  if (!gates) return error ? <p role="alert" className="text-red-600">{error}</p> : <p className="text-zinc-400">Checking the video file…</p>;
  // Rights and originality only appear when the owner turned enforcement on in Settings.
  // The file check always applies: it stops a broken MP4 from being uploaded.
  if (!gates.enforced) return gates.gates.technical.passed ? null : <p role="alert" className="text-red-700">✕ The video file failed its check: {gates.gates.technical.reason}</p>;
  const items = [['Technical', gates.gates.technical], ['Rights', gates.gates.rights], ['Originality', gates.gates.originality]] as const;
  const rights = gates.gates.rights;
  return <div className="border rounded-xl p-3 space-y-2" aria-label="Publish gates">
    <p className="font-semibold flex items-center gap-1.5">{gates.passed ? <ShieldCheck size={14} className="text-emerald-600" /> : <ShieldAlert size={14} className="text-amber-600" />}Publish gates · {gates.passed ? 'all passed' : 'blocked'}</p>
    <ul className="space-y-1">{items.map(([name, gate]) => <li key={name} className="flex gap-2">
      <span className={`shrink-0 font-semibold ${gate.passed ? 'text-emerald-700' : 'text-red-700'}`}>{gate.passed ? '✓' : '✕'} {name}{gate.status === 'ATTESTED' ? ' (owner-attested)' : ''}</span>
      <span className="text-zinc-500">{gate.reason}</span></li>)}</ul>
    {!rights.passed && <details open><summary className="cursor-pointer font-medium">Record your rights basis for these sources</summary>
      <div className="space-y-2 mt-2">
        <p className="text-zinc-500">Downloadable is not reusable. Only record this if you can show the basis. The statement is stored with the exact sources and is void if the footage changes.</p>
        <label className="block">How do you hold the rights?<select aria-label="Rights basis" className="creator-select mt-1" value={basis} onChange={e => setBasis(e.target.value)}>{Object.entries(BASES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        <label className="block">Who granted it and where is the proof?<textarea aria-label="Rights evidence note" className="creator-select mt-1 min-h-16" value={note} onChange={e => setNote(e.target.value)} placeholder="e.g. Written licence from the creator, saved in Drive / Licences / 2025" /></label>
        <label className="block">Link to proof (optional)<input aria-label="Rights evidence link" className="creator-select mt-1" value={evidence} onChange={e => setEvidence(e.target.value)} placeholder="https://" /></label>
        <button type="button" disabled={busy || note.trim().length < 10} onClick={save} className="w-full h-9 rounded-xl bg-zinc-900 text-white font-medium disabled:opacity-40">{busy ? 'Saving…' : 'Record rights basis'}</button>
      </div></details>}
    {error && <p role="alert" className="text-red-600">{error}</p>}
  </div>;
};
