import React from 'react';
import { Check, Circle } from 'lucide-react';

interface ProgressStagesProps {
  mode: 'viral' | 'ranking';
  status: string;
  progress: number;
}

interface StageItem {
  id: string;
  label: string;
  backendStatuses: string[];
}

const RANKING_STAGES: StageItem[] = [
  { id: 'topic', label: 'Understanding topic', backendStatuses: ['INGESTING'] },
  { id: 'sources', label: 'Finding candidate videos', backendStatuses: ['INGESTING'] },
  { id: 'analysis', label: 'Analyzing videos', backendStatuses: ['ANALYZING'] },
  { id: 'moments', label: 'Finding best moments', backendStatuses: ['ANALYZING'] },
  { id: 'ranking', label: 'Ranking moments', backendStatuses: ['RANKING'] },
  { id: 'editing', label: 'Editing & Rendering', backendStatuses: ['EDITING', 'RENDERING'] },
  { id: 'cleanup', label: 'Cleaning temporary files', backendStatuses: ['CLEANING'] },
];

const VIRAL_STAGES: StageItem[] = [
  { id: 'ingest', label: 'Ingesting source video', backendStatuses: ['INGESTING'] },
  { id: 'transcribe', label: 'Transcribing speech & audio', backendStatuses: ['TRANSCRIBING'] },
  { id: 'detect', label: 'Detecting viral moments', backendStatuses: ['ANALYZING'] },
  { id: 'score', label: 'Scoring viral potential', backendStatuses: ['ANALYZING'] },
  { id: 'reframe', label: 'Reframing 9:16 vertical', backendStatuses: ['EDITING'] },
  { id: 'captions', label: 'Adding dynamic captions', backendStatuses: ['EDITING', 'RENDERING'] },
  { id: 'cleanup', label: 'Cleaning temporary files', backendStatuses: ['CLEANING'] },
];

export const ProgressStages: React.FC<ProgressStagesProps> = ({ mode, status, progress }) => {
  const stages = mode === 'viral' ? VIRAL_STAGES : RANKING_STAGES;

  const getStageStatus = (stage: StageItem, index: number) => {
    if (status === 'COMPLETED') return 'completed';
    if (status === 'FAILED' || status === 'CANCELLED') return 'failed';

    const current = stages.findIndex(s => s.backendStatuses.includes(status));
    const matching = stages.map((s, i) => s.backendStatuses.includes(status) ? i : -1).filter(i => i >= 0);
    if (current < 0) return 'pending';
    if (matching.includes(index)) return 'active';
    if (index < current) return 'completed';
    return 'pending';
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5 py-1">
      {stages.map((stage, idx) => {
        const stageState = getStageStatus(stage, idx);
        return (
          <div key={stage.id} className="flex items-center gap-2.5 text-xs">
            {stageState === 'completed' && (
              <div className="w-4 h-4 rounded-full bg-zinc-900 text-white flex items-center justify-center shrink-0">
                <Check className="w-2.5 h-2.5 stroke-[2.5]" />
              </div>
            )}
            {stageState === 'active' && (
              <div className="w-4 h-4 rounded-full bg-zinc-100 border border-zinc-900 flex items-center justify-center shrink-0">
                <div className="w-1.5 h-1.5 rounded-full bg-zinc-900 animate-pulse" />
              </div>
            )}
            {stageState === 'pending' && (
              <div className="w-4 h-4 rounded-full border border-zinc-200 flex items-center justify-center shrink-0 text-zinc-300">
                <Circle className="w-2.5 h-2.5" />
              </div>
            )}
            {stageState === 'failed' && (
              <div className="w-4 h-4 rounded-full bg-red-100 text-red-600 flex items-center justify-center shrink-0 font-bold text-[9px]">
                ✕
              </div>
            )}
            <span
              className={`font-medium ${
                stageState === 'completed'
                  ? 'text-zinc-800'
                  : stageState === 'active'
                  ? 'text-zinc-950 font-semibold'
                  : 'text-zinc-400'
              }`}
            >
              {stage.label}
            </span>
          </div>
        );
      })}
    </div>
  );
};
