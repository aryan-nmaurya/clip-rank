import React from 'react';
import { X, Download } from 'lucide-react';
import { OutputClip } from '../types';

interface VideoModalProps {
  clip: OutputClip | null;
  onClose: () => void;
}

export const VideoModal: React.FC<VideoModalProps> = ({ clip, onClose }) => {
  if (!clip || !clip.video_path) return null;

  const downloadUrl = `/api/clips/${clip.id}/download`;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4 animate-in fade-in duration-150"
      onClick={onClose}
    >
      <div
        className="relative bg-zinc-950 rounded-2xl max-w-sm w-full overflow-hidden shadow-2xl border border-zinc-800 flex flex-col items-center"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Top bar */}
        <div className="w-full flex items-center justify-between p-3.5 border-b border-zinc-800/80 bg-zinc-900/40">
          <span className="text-xs font-medium text-zinc-300 truncate max-w-[240px]">
            {clip.title}
          </span>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded-full bg-zinc-800 hover:bg-zinc-700 text-zinc-300 flex items-center justify-center transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* 9:16 Video Player Container */}
        <div className="w-full aspect-[9/16] bg-black relative flex items-center justify-center max-h-[72vh]">
          <video
            src={clip.video_path}
            controls
            autoPlay
            playsInline
            className="w-full h-full object-contain"
          />
        </div>

        {/* Bottom bar with Download */}
        <div className="w-full p-4 bg-zinc-900 flex items-center justify-between gap-3 border-t border-zinc-800">
          <div className="flex flex-col">
            <span className="text-xs font-semibold text-white truncate max-w-[170px]">
              {clip.title}
            </span>
            <span className="text-[11px] text-zinc-400">
              {clip.viral_score ? `Potential ${clip.viral_score}/100` : clip.subtitle || 'Short video'} · {Math.round(clip.duration)}s
            </span>
          </div>

          <a
            href={downloadUrl}
            download
            className="px-4 py-2 rounded-xl bg-white hover:bg-zinc-200 text-zinc-900 font-medium text-xs flex items-center gap-1.5 transition-colors shadow-sm"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Download</span>
          </a>
        </div>
      </div>
    </div>
  );
};
