import React from 'react';
import { Check, Circle } from 'lucide-react';

interface ProgressStagesProps {
  mode: 'viral' | 'ranking' | 'discovery' | 'movie';
  status: string;
  progress: number;
  format?: string;
}

interface StageItem {
  id: string;
  label: string;
  backendStatuses: string[];
  minimumProgress?: number;
}

const RANKING_STAGES: StageItem[] = [
  { id: 'connections', label: 'Checking vision and natural voice', backendStatuses: ['ANALYZING'], minimumProgress: 4 },
  { id: 'sources', label: 'Finding raw candidate videos', backendStatuses: ['INGESTING'], minimumProgress: 9 },
  { id: 'analysis', label: 'Verifying footage and best moments', backendStatuses: ['ANALYZING'], minimumProgress: 20 },
  { id: 'ranking', label: 'Ranking verified payoffs', backendStatuses: ['RANKING'], minimumProgress: 53 },
  { id: 'writing', label: 'Writing distinct A/B stories', backendStatuses: ['SCRIPTING'], minimumProgress: 55 },
  { id: 'voice', label: 'Generating narration and captions', backendStatuses: ['GENERATING_VOICE'], minimumProgress: 60 },
  { id: 'editing', label: 'Editing and mixing audio', backendStatuses: ['EDITING', 'RENDERING'], minimumProgress: 82 },
  { id: 'review', label: 'Reviewing the finished videos', backendStatuses: ['ANALYZING'], minimumProgress: 89 },
  { id: 'cleanup', label: 'Cleaning temporary files', backendStatuses: ['CLEANING'], minimumProgress: 98 },
];

const VIRAL_STAGES: StageItem[] = [
  { id: 'ingest', label: 'Ingesting source video', backendStatuses: ['INGESTING'], minimumProgress: 10 },
  { id: 'transcribe', label: 'Transcribing speech & audio', backendStatuses: ['TRANSCRIBING'], minimumProgress: 25 },
  { id: 'detect', label: 'Finding complete stories', backendStatuses: ['ANALYZING'], minimumProgress: 42 },
  { id: 'writing', label: 'Writing a grounded hook', backendStatuses: ['SCRIPTING'], minimumProgress: 50 },
  { id: 'voice', label: 'Generating narration and captions', backendStatuses: ['GENERATING_VOICE'], minimumProgress: 57 },
  { id: 'reframe', label: 'Reframing 9:16 vertical', backendStatuses: ['EDITING'], minimumProgress: 66 },
  { id: 'mix', label: 'Rendering and mixing audio', backendStatuses: ['RENDERING'], minimumProgress: 77 },
  { id: 'review', label: 'Reviewing the finished Short', backendStatuses: ['QC'], minimumProgress: 86 },
  { id: 'cleanup', label: 'Cleaning temporary files', backendStatuses: ['CLEANING'], minimumProgress: 98 },
];
const MOVIE_STAGES:StageItem[]=[
  {id:'analyze',label:'Analyzing video',backendStatuses:['INGESTING','TRANSCRIBING'],minimumProgress:8},
  {id:'moments',label:'Finding strong moments',backendStatuses:['ANALYZING'],minimumProgress:32},
  {id:'styles',label:'Choosing best styles',backendStatuses:['ANALYZING'],minimumProgress:55},
  {id:'create',label:'Creating clips',backendStatuses:['GENERATING_VOICE','EDITING','RENDERING'],minimumProgress:60},
  {id:'review',label:'Final quality check',backendStatuses:['QC'],minimumProgress:86},
  {id:'ready',label:'Ready',backendStatuses:['CLEANING'],minimumProgress:98},
];
const LEGACY_DISCOVERY_STAGES:StageItem[]=[
  {id:'research',label:'Researching primary sources',backendStatuses:['RESEARCHING']},
  {id:'writing',label:'Writing and fact-checking',backendStatuses:['SCRIPTING']},
  {id:'voice',label:'Generating natural narration',backendStatuses:['VOICE']},
  {id:'edit',label:'Creating original diagrams',backendStatuses:['EDITING']},
  {id:'render',label:'Rendering finished video',backendStatuses:['RENDERING']},
  {id:'qc',label:'Reviewing the final video',backendStatuses:['QC']},
];
const DISCOVERY_STAGES:StageItem[]=[
  {id:'source',label:'Loading verified footage',backendStatuses:['INGESTING']},
  {id:'speech',label:'Timing source speech',backendStatuses:['TRANSCRIBING']},
  {id:'story',label:'Planning the visible story',backendStatuses:['ANALYZING','SCRIPTING','RANKING']},
  {id:'voice',label:'Recording original commentary',backendStatuses:['GENERATING_VOICE','VOICE']},
  {id:'edit',label:'Editing footage and captions',backendStatuses:['EDITING']},
  {id:'render',label:'Rendering and mixing audio',backendStatuses:['RENDERING']},
  {id:'qc',label:'Checking the finished video',backendStatuses:['QC']},
  {id:'cleanup',label:'Removing temporary files',backendStatuses:['CLEANING']},
];

export const ProgressStages: React.FC<ProgressStagesProps> = ({ mode, status, progress, format }) => {
  const stages = mode === 'movie' ? MOVIE_STAGES : mode === 'discovery' ? (format==='explainer'?LEGACY_DISCOVERY_STAGES:DISCOVERY_STAGES) : mode === 'viral' ? VIRAL_STAGES : RANKING_STAGES;

  const getStageStatus = (stage: StageItem, index: number) => {
    if (status === 'COMPLETED') return 'completed';
    if (mode === 'ranking' || mode === 'movie' || mode === 'viral') {
      const activeStage = mode === 'viral' ? stages.findIndex(item => item.backendStatuses.includes(status)) : -1;
      const current = activeStage >= 0 ? activeStage : stages.reduce((last, item, i) => progress >= (item.minimumProgress ?? 100) ? i : last, -1);
      if (index < current) return 'completed';
      if (index === current) return status === 'FAILED' || status === 'CANCELLED' ? 'failed' : 'active';
      return 'pending';
    }
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
