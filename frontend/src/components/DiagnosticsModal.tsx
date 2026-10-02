import React, { useEffect, useState } from 'react';
import { X, CheckCircle2, AlertCircle, Cpu, HardDrive, RefreshCw } from 'lucide-react';
import { Diagnostics } from '../types';
import { getDiagnostics } from '../api';

interface DiagnosticsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const DiagnosticsModal: React.FC<DiagnosticsModalProps> = ({ isOpen, onClose }) => {
  const [data, setData] = useState<Diagnostics | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchDiag = async () => {
    setLoading(true);
    try {
      const res = await getDiagnostics();
      setData(res);
    } catch (e) {
      // ignore
    } finally {
      setLoading(false);
    }
  };

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
        className="bg-white rounded-2xl max-w-md w-full p-6 shadow-xl border border-zinc-200"
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
