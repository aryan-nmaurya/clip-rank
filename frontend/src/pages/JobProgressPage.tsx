import React, { useEffect, useState } from 'react';
import {
  X,
  RefreshCw,
  Trash2,
  CheckCircle2,
  AlertCircle,
  Download,
  Film,
  Play,
  Flame,
  Award,
} from 'lucide-react';
import { Project, OutputClip } from '../types';
import { getProject, cancelJob, deleteProject, regenerateProject, subscribeToJobEvents } from '../api';
import { ProgressStages } from '../components/ProgressStages';
import { VideoModal } from '../components/VideoModal';

interface JobProgressPageProps {
  projectId: string;
  jobId: string;
  onNavigateBack: () => void;
}

export const JobProgressPage: React.FC<JobProgressPageProps> = ({ projectId, jobId, onNavigateBack }) => {
  const [project, setProject] = useState<Project | null>(null);
  const [selectedClip, setSelectedClip] = useState<OutputClip | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [currentJobId, setCurrentJobId] = useState(jobId);
  const [error, setError] = useState('');
  const [fetchError, setFetchError] = useState('');
  useEffect(() => { setCurrentJobId(jobId); }, [jobId]);

  const fetchProjectData = async () => {
    try {
      const data = await getProject(projectId);
      setProject(data);
      setFetchError('');
    } catch (err: any) { setFetchError(err.message || 'This project is unavailable.'); }
  };

  useEffect(() => {
    fetchProjectData();

    // Subscribe to real-time SSE events
    const unsubscribe = subscribeToJobEvents(
      currentJobId,
      (eventData) => {
        setProject((prev) => {
          if (!prev) return prev;
          const updatedJob = prev.job ? { ...prev.job, ...eventData, current_stage: eventData.stage } : undefined;
          return {
            ...prev,
            status: ['COMPLETED', 'FAILED', 'CANCELLED'].includes(eventData.status) ? eventData.status : prev.status,
            job: updatedJob as any
          };
        });

        if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(eventData.status)) {
          fetchProjectData();
        }
      }
    );

    const interval = setInterval(fetchProjectData, 3500);

    return () => {
      unsubscribe();
      clearInterval(interval);
    };
  }, [projectId, currentJobId]);

  const handleCancel = async () => {
    if (!confirm('Cancel this active generation?')) return;
    setActionLoading(true);
    try {
      await cancelJob(currentJobId);
      await fetchProjectData();
    } catch (err: any) {
      setError(err.message || 'Could not complete this action');
    } finally {
      setActionLoading(false);
    }
  };

  const handleRegenerate = async () => {
    setActionLoading(true);
    try {
      const response = await regenerateProject(projectId);
      setCurrentJobId(response.job_id);
      await fetchProjectData();
    } catch (err: any) {
      setError(err.message || 'Could not complete this action');
    } finally {
      setActionLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm('Delete this project?')) return;
    setActionLoading(true);
    try {
      await deleteProject(projectId);
      onNavigateBack();
    } catch (err: any) {
      setError(err.message || 'Could not complete this action');
    } finally {
      setActionLoading(false);
    }
  };

  if (fetchError) return <div className="flex-1 p-8 flex flex-col items-center justify-center gap-4"><p className="text-sm text-zinc-500">{fetchError}</p><button onClick={onNavigateBack} className="rounded-xl bg-zinc-900 text-white px-5 py-3 text-sm">Back to library</button></div>;

  if (!project) {
    return (
      <div className="flex-1 h-screen flex items-center justify-center bg-[#FAF9F6] text-zinc-400 text-sm">
        Loading project...
      </div>
    );
  }

  const isCompleted = project.status === 'COMPLETED';
  const isFailed = project.status === 'FAILED';
  const isCancelled = project.status === 'CANCELLED';
  const isProcessing = !isCompleted && !isFailed && !isCancelled;
  const job = project.job;

  return (
    <div className="flex-1 h-screen overflow-y-auto bg-[#FAF9F6] p-8 flex flex-col items-center">
      <div className="max-w-4xl w-full flex flex-col gap-6 pb-16">
        {/* Header Bar */}
        <div className="flex items-start justify-between">
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded-md text-[10px] font-bold tracking-wider uppercase bg-zinc-200/70 text-zinc-800">
                {project.mode === 'viral' ? 'Viral Clips' : 'Ranking Video'}
              </span>
              <span className="text-zinc-400 text-xs">·</span>
              <span className="text-zinc-500 text-xs font-medium">
                {project.mode === 'viral' ? `${project.input_data.count || 3} clips requested` : '9:16 Vertical Short'}
              </span>
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-zinc-900">
              {project.title}
            </h1>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-2">
            {isProcessing && (
              <button
                onClick={handleCancel}
                disabled={actionLoading}
                className="h-8 px-3 rounded-lg border border-zinc-200 hover:bg-zinc-100 text-zinc-700 text-xs font-medium transition-colors"
              >
                Cancel
              </button>
            )}

            {(isCompleted || isFailed || isCancelled) && (
              <button
                onClick={handleRegenerate}
                disabled={actionLoading}
                className="h-8 px-3 rounded-lg border border-zinc-200 hover:bg-zinc-100 text-zinc-700 text-xs font-medium transition-colors flex items-center gap-1.5"
              >
                <RefreshCw className="w-3 h-3" />
                <span>{isCompleted ? 'Regenerate' : 'Retry'}</span>
              </button>
            )}

            <button
              onClick={handleDelete}
              disabled={actionLoading}
              title="Delete project"
              className="w-8 h-8 rounded-lg border border-zinc-200 hover:bg-red-50 hover:text-red-600 hover:border-red-200 text-zinc-500 flex items-center justify-center transition-colors"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Live Progress Card (When Processing or Failed) */}
        {!isCompleted && (
          <div className="bg-white rounded-2xl border border-zinc-200/90 shadow-sm p-6 flex flex-col gap-5">
            <div className="flex items-center justify-between">
              <div className="flex flex-col gap-1">
                <h2 className="text-base font-semibold text-zinc-900">
                  {isFailed ? "We couldn't finish this video." : isCancelled ? 'Generation cancelled' : 'Creating your short-form video…'}
                </h2>
                <p className="text-xs text-zinc-500">
                  {job?.current_stage || 'Processing pipeline stages autonomously.'}
                </p>
              </div>

              {isProcessing && (
                <div className="flex items-center gap-2 text-xs font-semibold text-zinc-800 bg-zinc-100 px-3 py-1 rounded-full">
                  <span className="w-2 h-2 rounded-full bg-zinc-900 animate-pulse" />
                  <span>{job?.progress || 10}%</span>
                </div>
              )}
            </div>

            {isFailed && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700 whitespace-pre-wrap break-words">{job?.error_message || 'Generation failed. Try another source or analysis engine.'}</div>}
            {/* Progress Bar */}
            {!isFailed && !isCancelled && (
              <div className="w-full h-2 rounded-full bg-zinc-100 overflow-hidden">
                <div
                  className="h-full bg-zinc-900 rounded-full transition-all duration-500"
                  style={{ width: `${Math.min(100, Math.max(5, job?.progress || 10))}%` }}
                />
              </div>
            )}

            {/* Stage Checklist (Section 7) */}
            <ProgressStages mode={project.mode} status={job?.status || 'QUEUED'} progress={job?.progress || 10} />
          </div>
        )}

        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        {project.result_data.warnings?.length > 0 && <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-xs text-amber-800 space-y-2">{project.result_data.warnings.map((warning: string) => <p key={warning}>{warning}</p>)}</div>}
        {/* Results Section (Section 41) */}
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-zinc-900">
              {isCompleted
                ? project.mode === 'viral' ? '✓ Your Viral Clips are ready' : '✓ Your Ranking Video is ready'
                : 'Generated Outputs'}
            </h3>
            {isCompleted && (
              <span className="text-xs text-emerald-700 font-semibold bg-emerald-50 px-2.5 py-0.5 rounded-full border border-emerald-200">
                Ready for download
              </span>
            )}
          </div>

          {/* Cards Grid */}
          <div className="flex flex-wrap gap-5 items-start">
            {project.clips.length > 0 ? (
              project.clips.map((clip) => {
                const downloadUrl = `/api/clips/${clip.id}/download`;
                return (
                  <div
                    key={clip.id}
                    className="bg-white rounded-2xl border border-zinc-200/90 overflow-hidden shadow-sm flex flex-col p-4 w-72 shrink-0 transition-all hover:shadow-md"
                  >
                    {/* 9:16 Video Thumbnail Container */}
                    <div
                      onClick={() => setSelectedClip(clip)}
                      className="w-full aspect-[9/16] rounded-xl bg-zinc-950 relative overflow-hidden group cursor-pointer flex items-center justify-center"
                    >
                      {clip.preview_path ? (
                        <img
                          src={clip.preview_path}
                          alt={clip.title}
                          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                        />
                      ) : (
                        <div className="w-full h-full bg-zinc-900 flex items-center justify-center">
                          <Film className="w-8 h-8 text-zinc-700" />
                        </div>
                      )}

                      {/* Play Button Overlay */}
                      <div className="absolute inset-0 bg-black/25 group-hover:bg-black/40 transition-colors flex items-center justify-center">
                        <div className="w-12 h-12 rounded-full bg-white/95 text-zinc-900 flex items-center justify-center shadow-lg group-hover:scale-110 active:scale-95 transition-all">
                          <Play className="w-5 h-5 fill-current ml-0.5" />
                        </div>
                      </div>

                      {/* Duration Tag */}
                      <div className="absolute bottom-2.5 right-2.5 px-2 py-0.5 rounded-md bg-black/75 backdrop-blur-sm text-white text-[11px] font-medium">
                        {Math.floor(clip.duration / 60)}:{(Math.floor(clip.duration % 60)).toString().padStart(2, '0')}
                      </div>

                      {/* Score or Rank Badge */}
                      {clip.viral_score && (
                        <div className="absolute top-2.5 left-2.5 px-2 py-0.5 rounded-md bg-emerald-600/90 text-white text-[11px] font-bold">
                          Potential {clip.viral_score}/100
                        </div>
                      )}
                      {clip.rank && (
                        <div className="absolute top-2.5 left-2.5 px-2 py-0.5 rounded-md bg-zinc-900/90 text-white text-[11px] font-bold">
                          #{clip.rank}
                        </div>
                      )}
                    </div>

                    {/* Clip Info */}
                    <div className="mt-3.5 flex flex-col gap-1">
                      <h4 className="text-sm font-semibold text-zinc-900 truncate" title={clip.title}>
                        {clip.title}
                      </h4>
                      <p className="text-xs text-zinc-500 line-clamp-2">
                        {clip.reason || clip.subtitle || 'Finished 9:16 vertical short'}
                      </p>
                    </div>

                    {/* Download Button */}
                    <div className="mt-4 pt-2 border-t border-zinc-100 flex items-center gap-2">
                      <a
                        href={downloadUrl}
                        download
                        className="flex-1 h-9 rounded-xl bg-zinc-900 hover:bg-zinc-800 text-white font-medium text-xs flex items-center justify-center gap-1.5 transition-colors shadow-sm"
                      >
                        <Download className="w-3.5 h-3.5" />
                        <span>Download MP4</span>
                      </a>
                    </div>
                  </div>
                );
              })
            ) : isProcessing ? (
              <div className="w-72 aspect-[9/16] rounded-2xl border-2 border-dashed border-zinc-200 bg-zinc-50/50 flex flex-col items-center justify-center p-6 text-center gap-3">
                <Film className="w-8 h-8 text-zinc-300 animate-pulse" />
                <span className="text-xs font-medium text-zinc-500">Rendering video...</span>
                <span className="text-[11px] text-zinc-400">Your footage is being selected and edited</span>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {project.result_data.moments?.length > 0 && <div className="max-w-4xl w-full bg-white border border-zinc-200 rounded-2xl p-5 mb-8">
        <h3 className="text-sm font-semibold mb-1">Selected moments & source credits</h3>
        <p className="text-xs text-zinc-400 mb-4">Potential scores are estimates based on source evidence. {project.result_data.analysis_basis}</p>
        <div className="space-y-3">{project.result_data.moments.map((moment: any, index: number) => <div key={index} className="border-t border-zinc-100 pt-3 text-xs flex gap-4">
          <span className="font-bold w-7 shrink-0">{moment.assigned_rank ? `#${moment.assigned_rank}` : index + 1}</span>
          <div className="flex-1"><p className="font-medium">{moment.label || moment.title}</p><p className="text-zinc-500 mt-1">{Number(moment.start).toFixed(1)}–{Number(moment.end).toFixed(1)}s · {moment.score}/100 · {moment.analysis_basis}</p><p className="text-zinc-500 mt-1">{moment.reason}</p>
            {moment.url && <a href={moment.url} target="_blank" rel="noreferrer" className="inline-block mt-1 underline text-zinc-600">Source{moment.creator ? ` · ${moment.creator}` : ''}</a>}</div>
        </div>)}</div>
      </div>}
      {/* Video Modal Player */}
      <VideoModal clip={selectedClip} onClose={() => setSelectedClip(null)} />
    </div>
  );
};
