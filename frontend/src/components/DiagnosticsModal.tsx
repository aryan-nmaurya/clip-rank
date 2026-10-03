import React, { useEffect, useState } from 'react';
import { X, CheckCircle2, AlertCircle, Cpu, HardDrive, RefreshCw, Play, Loader2, MinusCircle } from 'lucide-react';
import { Diagnostics, Health, StorageReport, ProductionTestState } from '../types';
import { getDiagnostics, getHealth, getStorage, cleanupStorage, startProductionTest, getProductionTest } from '../api';

interface DiagnosticsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const DiagnosticsModal: React.FC<DiagnosticsModalProps> = ({ isOpen, onClose }) => {
  const [data, setData] = useState<Diagnostics | null>(null);
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [storage, setStorage] = useState<StorageReport | null>(null);
  const [test, setTest] = useState<ProductionTestState>({ status: 'idle' });
  const [error, setError] = useState('');

  const fetchDiag = async () => {
    setLoading(true);
    setError('');
    const [diag, healthReport, storageReport, testState] = await Promise.allSettled([getDiagnostics(), getHealth(), getStorage(), getProductionTest()]);
    if (diag.status === 'fulfilled') setData(diag.value);
    if (healthReport.status === 'fulfilled') setHealth(healthReport.value);
    if (storageReport.status === 'fulfilled') setStorage(storageReport.value);
    if (testState.status === 'fulfilled') setTest(testState.value);
    const failed = [diag, healthReport, storageReport].find(r => r.status === 'rejected') as PromiseRejectedResult | undefined;
    if (failed) setError(`Could not load all diagnostics: ${failed.reason?.message || 'the backend did not answer'}`);
    setLoading(false);
  };

  const runTest = async () => {
    setError('');
    try { setTest(await startProductionTest()); }
    catch (e: any) { setError(e.message); }
  };

  useEffect(() => {
    if (!isOpen || test.status !== 'running') return;
    const timer = setInterval(() => { getProductionTest().then(setTest).catch(e => setError(e.message)); }, 1500);
    return () => clearInterval(timer);
  }, [isOpen, test.status]);

  const clean = async () => {
    try { await cleanupStorage(); setStorage(await getStorage()); }
    catch (e: any) { setError(e.message); }
  };
  const gb = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(bytes < 10 * 1024 ** 3 ? 1 : 0)} GB`;

  useEffect(() => {
    if (isOpen) fetchDiag();
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-150"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl max-w-lg w-full max-h-[90vh] overflow-y-auto p-6 shadow-xl border border-zinc-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between pb-4 border-b border-zinc-100">
          <div className="flex items-center gap-2">
            <Cpu className="w-5 h-5 text-zinc-700" />
            <h3 className="font-semibold text-zinc-900 text-sm">System Diagnostics</h3>
          </div>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded-lg hover:bg-zinc-100 text-zinc-400 hover:text-zinc-700 flex items-center justify-center"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="py-4 space-y-3.5 text-xs">
          {error && <p role="alert" className="text-red-600">{error}</p>}
          <section aria-label="System readiness" className="rounded-xl border border-zinc-200 p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-zinc-900">System readiness</span>
              <span className={`font-semibold ${health?.ready ? 'text-emerald-700' : 'text-amber-700'}`}>{health ? (health.ready ? 'READY' : 'NOT READY') : 'Checking…'}</span>
            </div>
            {health && !health.ready && health.problems.map(problem => <p key={problem.id} className="text-amber-800">{problem.detail} <span className="text-zinc-500">{problem.fix}</span></p>)}
            {health?.checks.map(check => <div key={check.id} className="flex items-start gap-2">
              {check.ok ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 mt-0.5 shrink-0" /> : check.required ? <AlertCircle className="w-3.5 h-3.5 text-amber-600 mt-0.5 shrink-0" /> : <MinusCircle className="w-3.5 h-3.5 text-zinc-400 mt-0.5 shrink-0" />}
              <span className="text-zinc-700"><span className="font-medium">{check.label}</span> <span className="text-zinc-500">· {check.detail}</span></span></div>)}
          </section>
          <section aria-label="Production test" className="rounded-xl border border-zinc-200 p-3 space-y-2">
            <div className="flex items-center justify-between gap-3">
              <div><p className="font-semibold text-zinc-900">Production test</p><p className="text-zinc-500">Makes a real Short from a ClipRank-generated test asset and checks every stage.</p></div>
              <button onClick={runTest} disabled={test.status === 'running'} className="shrink-0 px-3 py-2 rounded-lg bg-zinc-900 text-white font-medium flex items-center gap-1.5 disabled:opacity-50">
                {test.status === 'running' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}Run production test</button>
            </div>
            {test.steps?.map(step => <div key={step.name} className="flex items-start gap-2" data-status={step.status}>
              <span className="w-4 shrink-0 text-center">{step.status === 'passed' ? '✓' : step.status === 'failed' ? '✕' : step.status === 'running' ? '…' : '·'}</span>
              <span className={step.status === 'failed' ? 'text-red-700' : 'text-zinc-700'}><span className="font-medium">{step.name}</span>{step.detail ? <span className="text-zinc-500"> · {step.detail}</span> : null}</span></div>)}
            {test.status === 'done' && <p className={`font-semibold ${test.ready ? 'text-emerald-700' : 'text-red-700'}`}>{test.verdict}</p>}
            {test.status === 'done' && test.ready && <a href="/api/diagnostics/production-test/video" target="_blank" rel="noreferrer" className="underline text-zinc-700">Watch cliprank-production-test.mp4</a>}
          </section>
          {storage && <section aria-label="Storage" className="rounded-xl border border-zinc-200 p-3 space-y-1.5">
            <div className="flex items-center justify-between"><span className="font-semibold text-zinc-900 flex items-center gap-1.5"><HardDrive className="w-3.5 h-3.5" />Storage used by ClipRank</span><span className="font-semibold">{gb(storage.total_bytes)}</span></div>
            <p className="text-zinc-500">Finished videos {gb(storage.bytes.output)} · working files {gb(storage.cache_bytes)} of {gb(storage.cache_budget_bytes)} allowed · models {gb(storage.bytes.models)} · {gb(storage.disk_free_bytes)} free on disk</p>
            {(storage.cache_over_budget || storage.disk_low) && <p className="text-amber-700">Storage is {storage.disk_low ? 'low' : 'over budget'}; clean up to reclaim space.</p>}
            <button onClick={clean} className="underline text-zinc-700">Clean up abandoned working files</button>
          </section>}
          <div className="flex items-center justify-between p-2.5 rounded-xl bg-zinc-50 border border-zinc-100">
            <span className="text-zinc-600 font-medium">Selected AI Mode</span>
            <span className="font-semibold text-zinc-900 uppercase tracking-wider">{data?.ai_provider || '—'}</span>
          </div>

          <div className="flex items-center justify-between p-2.5 rounded-xl bg-zinc-50 border border-zinc-100">
            <span className="text-zinc-600 font-medium">FFmpeg Engine</span>
            <div className="flex items-center gap-1.5 font-medium">
              {data?.ffmpeg_available ? (
                <CheckCircle2 className="w-4 h-4 text-emerald-600" />
              ) : (
                <AlertCircle className="w-4 h-4 text-amber-500" />
              )}
              <span className={data?.ffmpeg_available ? 'text-zinc-800' : 'text-amber-600'}>
                {data?.ffmpeg_version || 'Not Detected'}
              </span>
            </div>
          </div>

          <div className="flex items-center justify-between p-2.5 rounded-xl bg-zinc-50 border border-zinc-100">
            <span className="text-zinc-600 font-medium">Google AI Studio (Gemini)</span>
            <div className="flex items-center gap-1.5 font-medium">
              {data?.gemini_configured ? (
                <CheckCircle2 className="w-4 h-4 text-emerald-600" />
              ) : (
                <AlertCircle className="w-4 h-4 text-zinc-400" />
              )}
              <span className={data?.gemini_configured ? 'text-emerald-700 font-semibold' : 'text-zinc-500'}>
                {data?.gemini_configured ? 'Ready' : 'Not Configured'}
              </span>
            </div>
          </div>

          <div className="flex items-center justify-between p-2.5 rounded-xl bg-zinc-50 border border-zinc-100">
            <span className="text-zinc-600 font-medium">Local AI (Ollama Endpoint)</span>
            <div className="flex items-center gap-1.5 font-medium">
              {data?.local_endpoint_reachable ? (
                <CheckCircle2 className="w-4 h-4 text-emerald-600" />
              ) : (
                <AlertCircle className="w-4 h-4 text-zinc-400" />
              )}
              <span className={data?.local_endpoint_reachable ? 'text-emerald-700' : 'text-zinc-500'}>
                {data?.local_endpoint_reachable ? 'Connected' : 'Offline / Standby'}
              </span>
            </div>
          </div>

          <div className="flex items-center justify-between p-2.5 rounded-xl bg-zinc-50 border border-zinc-100">
            <span className="text-zinc-600 font-medium">Total Rendered Projects</span>
            <span className="font-semibold text-zinc-900">{data?.completed_projects ?? 0} / {data?.total_projects ?? 0}</span>
          </div>
        </div>

        <div className="pt-3 border-t border-zinc-100 flex items-center justify-between">
          <button
            onClick={fetchDiag}
            disabled={loading}
            className="text-xs text-zinc-500 hover:text-zinc-900 flex items-center gap-1.5 font-medium"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl bg-zinc-900 hover:bg-zinc-800 text-white font-medium text-xs transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
};
