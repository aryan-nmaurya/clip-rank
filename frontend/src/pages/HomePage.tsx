import React from 'react';
import { Flame, Award, ArrowRight, Sparkles, Video, Play } from 'lucide-react';

interface HomePageProps {
  onSelectFlow: (flow: 'viral' | 'ranking') => void;
}

export const HomePage: React.FC<HomePageProps> = ({ onSelectFlow }) => {
  return (
    <div className="flex-1 h-screen overflow-y-auto flex flex-col items-center justify-center p-8 bg-[#FAF9F6]">
      <div className="max-w-3xl w-full flex flex-col gap-10 pb-12">
        {/* Header */}
        <div className="flex flex-col gap-2 text-center items-center">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-zinc-100 text-zinc-700 text-xs font-semibold uppercase tracking-wider mb-2">
            <Sparkles className="w-3.5 h-3.5 text-zinc-900" />
            <span>AI Shorts Creator</span>
          </div>
          <h1 className="text-4xl font-extrabold tracking-tight text-zinc-900">
            What do you want to create?
          </h1>
          <p className="text-zinc-500 text-sm max-w-md">
            Two ways to build your next reel: find highlights in a video, or discover and rank a collection of moments.
          </p>
        </div>

        {/* The Two Primary Workflows (Section 1 & 4) */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Workflow A: Viral Clips */}
          <div
            onClick={() => onSelectFlow('viral')}
            className="group relative bg-white rounded-3xl border border-zinc-200/90 hover:border-zinc-900/40 p-8 shadow-sm hover:shadow-xl transition-all duration-300 cursor-pointer flex flex-col justify-between gap-6 overflow-hidden active:scale-[0.99]"
          >
            <div className="flex flex-col gap-4">
              <div className="w-14 h-14 rounded-2xl bg-rose-50 text-rose-600 flex items-center justify-center group-hover:scale-110 group-hover:bg-rose-600 group-hover:text-white transition-all duration-300 shadow-sm">
                <Flame className="w-7 h-7" />
              </div>
              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-bold text-rose-600 uppercase tracking-wider">Workflow A</span>
                <h3 className="text-xl font-bold text-zinc-900 group-hover:text-zinc-950">Viral Clips</h3>
                <p className="text-zinc-500 text-xs leading-relaxed">
                  Upload a video or paste a public link. Select complete highlights, preserve the footage and audio, and add captions timed to the actual speech.
                </p>
              </div>
            </div>

            <div className="flex items-center justify-between pt-4 border-t border-zinc-100">
              <span className="text-xs font-semibold text-zinc-800 group-hover:text-zinc-950">
                Input video → Multiple viral clips
              </span>
              <div className="w-8 h-8 rounded-full bg-zinc-100 group-hover:bg-zinc-900 group-hover:text-white text-zinc-700 flex items-center justify-center transition-colors">
                <ArrowRight className="w-4 h-4" />
              </div>
            </div>
          </div>

          {/* Workflow B: Ranking Video */}
          <div
            onClick={() => onSelectFlow('ranking')}
            className="group relative bg-white rounded-3xl border border-zinc-200/90 hover:border-zinc-900/40 p-8 shadow-sm hover:shadow-xl transition-all duration-300 cursor-pointer flex flex-col justify-between gap-6 overflow-hidden active:scale-[0.99]"
          >
            <div className="flex flex-col gap-4">
              <div className="w-14 h-14 rounded-2xl bg-amber-50 text-amber-600 flex items-center justify-center group-hover:scale-110 group-hover:bg-amber-600 group-hover:text-white transition-all duration-300 shadow-sm">
                <Award className="w-7 h-7" />
              </div>
              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-bold text-amber-600 uppercase tracking-wider">Workflow B</span>
                <h3 className="text-xl font-bold text-zinc-900 group-hover:text-zinc-950">Ranking Video</h3>
                <p className="text-zinc-500 text-xs leading-relaxed">
                  Find videos by topic or supply your own clips. Compare their strongest moments and create a countdown with a bold title, colored ranking list, and original audio.
                </p>
              </div>
            </div>

            <div className="flex items-center justify-between pt-4 border-t border-zinc-100">
              <span className="text-xs font-semibold text-zinc-800 group-hover:text-zinc-950">
                Enter topic → Finished #5 → #1 video
              </span>
              <div className="w-8 h-8 rounded-full bg-zinc-100 group-hover:bg-zinc-900 group-hover:text-white text-zinc-700 flex items-center justify-center transition-colors">
                <ArrowRight className="w-4 h-4" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
