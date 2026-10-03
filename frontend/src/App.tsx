import React, { useState, useEffect } from 'react';
import { Sidebar } from './components/Sidebar';
import { HomePage } from './pages/HomePage';
import { ViralPage } from './pages/ViralPage';
import { RankingPage } from './pages/RankingPage';
import { MoviePage } from './pages/MoviePage';
import { JobProgressPage } from './pages/JobProgressPage';
import { LibraryPage } from './pages/LibraryPage';
import { SettingsPage } from './pages/SettingsPage';
import {DiscoveryPage} from './pages/DiscoveryPage';
import {AutopilotPage} from './pages/AutopilotPage';
import { DiagnosticsModal } from './components/DiagnosticsModal';
import { AIStatus } from './types';
import { getAIStatus } from './api';

export function App() {
  const [currentTab, setCurrentTab] = useState<'home' | 'viral' | 'ranking' | 'discovery' | 'movie' | 'autopilot' | 'progress' | 'library' | 'settings'>('home');
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [aiStatus, setAiStatus] = useState<AIStatus | null>(null);
  const [diagModalOpen, setDiagModalOpen] = useState(false);

  const refreshAIStatus = async () => {
    try {
      const res = await getAIStatus();
      setAiStatus(res);
    } catch {}
  };

  useEffect(() => {
    refreshAIStatus();
    const interval = setInterval(refreshAIStatus, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleStartJob = (jobId: string, projectId: string) => {
    setActiveJobId(jobId);
    setActiveProjectId(projectId);
    setCurrentTab('progress');
  };

  const handleOpenProject = (projectId: string, jobId: string) => {
    setActiveProjectId(projectId);
    setActiveJobId(jobId);
    setCurrentTab('progress');
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#FAF9F6] text-zinc-900 font-sans selection:bg-zinc-900 selection:text-white">
      {/* Sidebar */}
      <Sidebar
        currentTab={currentTab}
        onSelectTab={(tab) => {
          setCurrentTab(tab);
          setActiveProjectId(null);
          setActiveJobId(null);
        }}
        aiStatus={aiStatus}
        onOpenDiagnostics={() => setDiagModalOpen(true)}
      />

      {/* Main Content Area */}
      <main className="flex-1 h-screen flex flex-col overflow-hidden">
        {currentTab === 'home' && (
          <HomePage onSelectFlow={(flow) => setCurrentTab(flow)} />
        )}

        {currentTab === 'viral' && (
          <ViralPage onStartJob={handleStartJob} />
        )}

        {currentTab === 'ranking' && (
          <RankingPage onStartJob={handleStartJob} aiStatus={aiStatus} onOpenSettings={() => setCurrentTab('settings')} />
        )}
        {currentTab==='discovery'&&<DiscoveryPage onStartJob={handleStartJob}/>}
        {currentTab==='movie'&&<MoviePage onStartJob={handleStartJob}/>}
        {currentTab==='autopilot'&&<AutopilotPage onOpenProject={handleOpenProject} onSettings={()=>setCurrentTab('settings')}/>}

        {currentTab === 'progress' && activeProjectId && activeJobId && (
          <JobProgressPage
            projectId={activeProjectId}
            jobId={activeJobId}
            onNavigateBack={() => setCurrentTab('library')}
            onTryMovieSource={() => setCurrentTab('movie')}
          />
        )}

        {currentTab === 'library' && (
          <LibraryPage onOpenProject={handleOpenProject} />
        )}

        {currentTab === 'settings' && (
          <SettingsPage onSettingsUpdated={refreshAIStatus} />
        )}
      </main>

      {/* Diagnostics Modal */}
      <DiagnosticsModal
        isOpen={diagModalOpen}
        onClose={() => setDiagModalOpen(false)}
      />
    </div>
  );
}

export default App;
