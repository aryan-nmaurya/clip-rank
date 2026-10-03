import React, { useEffect, useState } from 'react';
import { CheckCircle2, AlertTriangle, Play } from 'lucide-react';
import { getHealth, getProductionTest } from '../api';
import { Health, ProductionTestState } from '../types';

/** First-run setup: tells the user whether ClipRank can actually make a Short before they try. */
export const SetupCard: React.FC<{ onOpenDiagnostics: () => void }> = ({ onOpenDiagnostics }) => {
  const [health, setHealth] = useState<Health | null>(null);
  const [test, setTest] = useState<ProductionTestState | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([getHealth(), getProductionTest()]).then(([h, t]) => { setHealth(h); setTest(t); })
      .catch(e => setError(e.message));
  }, []);

  if (error) return <div role="alert" className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">Could not check system readiness: {error}</div>;
  if (!health || !test) return null;
  if (!health.ready) return <div role="alert" className="rounded-2xl border border-amber-300 bg-amber-50 p-5 flex gap-3">
    <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
    <div className="text-sm text-amber-950"><p className="font-semibold">Finish setup before creating</p>
      {health.problems.map(p => <p key={p.id} className="mt-1">{p.detail} <span className="text-amber-800">{p.fix}</span></p>)}
      <button onClick={onOpenDiagnostics} className="mt-3 text-xs font-semibold underline">Open system diagnostics</button></div></div>;
  if (!(test.status === 'done' && test.ready)) return <div className="rounded-2xl border border-zinc-200 bg-white p-5 flex items-center justify-between gap-4">
    <div className="text-sm"><p className="font-semibold text-zinc-900">Everything is installed. Prove it works.</p>
      <p className="text-zinc-500 mt-1">The production test makes a real Short from a generated test asset and checks every stage.</p></div>
    <button onClick={onOpenDiagnostics} className="shrink-0 px-4 py-2.5 rounded-xl bg-zinc-900 text-white text-xs font-semibold flex items-center gap-1.5"><Play size={13} />Run production test</button></div>;
  return <p className="text-xs text-emerald-700 flex items-center gap-1.5 justify-center"><CheckCircle2 size={14} />System ready · last production test passed</p>;
};
