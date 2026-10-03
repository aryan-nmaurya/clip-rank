import { Project, Job, Settings, AIStatus, Diagnostics, YouTubeConnection, YouTubeUpload, Health, StorageReport, ProductionTestState, ClipGates } from './types';

const API_BASE = '/api';

export async function createMovieProject(data:FormData):Promise<{job_id:string;project_id:string}>{
  const response=await fetch(`${API_BASE}/projects/movie`,{method:'POST',body:data});
  if(!response.ok){const error=await response.json().catch(()=>({}));throw new Error(error.detail||'Movie generation could not start.');}
  return response.json();
}
export async function importMovieMusic(data:FormData):Promise<any>{
  const response=await fetch(`${API_BASE}/movie/music`,{method:'POST',body:data});
  if(!response.ok){const error=await response.json().catch(()=>({}));throw new Error(error.detail||'Music rights or audio could not be validated.');}
  return response.json();
}

export async function studioRequest(path:string,payload?:unknown):Promise<any> {
  const response=await fetch(`${API_BASE}/studio${path}`,payload===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Check your studio settings and try again.');
  return data;
}

async function youtubeRequest(path: string, payload?: unknown) {
  const res = await fetch(`${API_BASE}/youtube${path}`, payload === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'Could not complete this YouTube action.');
  }
  return res.json();
}

export const getYouTubeConnection = (): Promise<YouTubeConnection> => youtubeRequest('/connection');
export const configureYouTube = (payload: { client_document?: unknown; privacy: string; made_for_kids: boolean }): Promise<YouTubeConnection> => youtubeRequest('/configure', payload);
export const connectYouTube = (): Promise<{ authorization_url: string }> => youtubeRequest('/connect', {});
export const disconnectYouTube = (): Promise<YouTubeConnection> => youtubeRequest('/disconnect', {});
export const getYouTubeUpload = (clipId: string): Promise<YouTubeUpload | null> => youtubeRequest(`/uploads/${encodeURIComponent(clipId)}`);
export const previewYouTubeDescription = (clipId:string,description:string):Promise<{description:string;tags:string[];tag_characters:number}> => youtubeRequest(`/uploads/${encodeURIComponent(clipId)}/preview`,{description});
export const updateYouTubeDescription = (clipId:string):Promise<YouTubeUpload> => youtubeRequest(`/uploads/${encodeURIComponent(clipId)}/description`,{});
export const getCopyrightCheck = (clipId:string) => youtubeRequest(`/copyright/${encodeURIComponent(clipId)}`);
export const refreshCopyrightCheck = (clipId:string) => youtubeRequest(`/copyright/${encodeURIComponent(clipId)}/check`,{});
export const confirmCopyrightCheck = (clipId:string,verdict:string,note:string) => youtubeRequest(`/copyright/${encodeURIComponent(clipId)}/review`,{verdict,note});
export const publishCopyrightCleared = (clipId:string) => youtubeRequest(`/copyright/${encodeURIComponent(clipId)}/publish`,{});
export const reuploadYouTube = (clipId: string): Promise<{ status: string }> => youtubeRequest(`/uploads/${encodeURIComponent(clipId)}/reupload`, {});
export const verifyYouTubeUpload = (clipId: string): Promise<YouTubeUpload | null> => youtubeRequest(`/uploads/${encodeURIComponent(clipId)}/verify`, {});
export const uploadToYouTube = (clipId: string, payload: { title: string; description: string; privacy: string; made_for_kids: boolean }): Promise<YouTubeUpload> => youtubeRequest(`/uploads/${encodeURIComponent(clipId)}`, payload);

export async function createViralProject(formData: FormData): Promise<{ project_id: string; job_id: string }> {
  const res = await fetch(`${API_BASE}/projects/viral`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to create viral clips project');
  }
  return res.json();
}

export async function createRankingProject(params: {
  topic: string;
  count?: number;
  ai_provider?: string;
  source_urls?: string[];
  narration?: boolean;
  layout?: 'fill' | 'fit' | 'smart';
  segment_duration?: number;
  voice?: string;
  variants?: 1 | 2;
  cc_only?: boolean;
  source_platforms?: string[];
}): Promise<{ project_id: string; job_id: string }> {
  const res = await fetch(`${API_BASE}/projects/ranking`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(typeof err.detail === 'string' ? err.detail : 'Check the topic, counts, and source links.');
  }
  return res.json();
}

export async function createRankingUpload(data: FormData): Promise<{ project_id: string; job_id: string }> {
  const response = await fetch(`${API_BASE}/projects/ranking/upload`, { method: 'POST', body: data });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'Failed to upload ranking sources');
  }
  return response.json();
}

export async function getVoicePreview(voice: string): Promise<Blob> {
  const response = await fetch(`${API_BASE}/speech/preview/${encodeURIComponent(voice)}`);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Could not preview this voice.');
  }
  return response.blob();
}

export async function getSpeechStatus(): Promise<any> {
  const response = await fetch(`${API_BASE}/speech/status`);
  if (!response.ok) throw new Error('Could not check the local voice engine.');
  return response.json();
}

export async function getVisionConnections(): Promise<any> {
  const response = await fetch(`${API_BASE}/vision/connections`);
  if (!response.ok) throw new Error('Could not check vision connections.');
  return response.json();
}

export async function testVisionConnection(provider: 'gemini' | 'local' | 'groq' | 'nvidia', settings: Settings): Promise<{ passed: boolean; message: string }> {
  const response = await fetch(`${API_BASE}/vision/test`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, gemini_api_key: settings.gemini_api_key, gemini_model: settings.gemini_model,
      gemini_api_key_2: settings.gemini_api_key_2, groq_api_key: settings.groq_api_key, groq_model: settings.groq_model || undefined,
      nvidia_api_key: settings.nvidia_api_key, nvidia_model: settings.nvidia_model || undefined,
      local_endpoint: settings.local_endpoint, local_model: settings.local_model }),
  });
  if (!response.ok) throw new Error('Could not test vision. Check your connection settings.');
  return response.json();
}

export async function listProjects(mode?: string, search?: string, status?: string): Promise<Project[]> {
  const params = new URLSearchParams();
  if (mode && mode !== 'all') params.set('mode', mode);
  if (search) params.set('search', search);
  if (status && status !== 'all') params.set('status', status);

  const url = `${API_BASE}/projects${params.toString() ? `?${params.toString()}` : ''}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error('Failed to list projects');
  return res.json();
}

export async function getProject(projectId: string): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects/${projectId}`);
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || 'This project could not be loaded.');
  }
  return res.json();
}

export interface Checkpoint { exists: boolean; stage?: string; verified?: number; judged?: number; sources?: number; pool_ready?: boolean; saved_at?: number }
export async function getCheckpoint(projectId: string): Promise<Checkpoint> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/checkpoint`);
  if (!res.ok || !(res.headers.get('content-type') || '').includes('json')) return { exists: false };
  return res.json();
}

export async function regenerateProject(projectId: string, fresh = false): Promise<{ job_id: string }> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/regenerate${fresh ? '?fresh=true' : ''}`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to regenerate project');
  return res.json();
}

export async function deleteProject(projectId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/projects/${projectId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to delete project');
}

export async function getJob(jobId: string): Promise<Job> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`);
  if (!res.ok) throw new Error('Failed to fetch job');
  return res.json();
}

export async function cancelJob(jobId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/cancel`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to cancel job');
}

export function subscribeToJobEvents(
  jobId: string,
  onMessage: (data: { status: string; progress: number; stage: string }) => void,
  onError?: (err: any) => void
): () => void {
  const es = new EventSource(`${API_BASE}/jobs/${jobId}/events`);
  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onMessage(data);
      if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(data.status)) es.close();
    } catch {}
  };
  es.onerror = (err) => {
    if (onError) onError(err);
  };
  return () => es.close();
}

export async function getSettings(): Promise<Settings> {
  const res = await fetch(`${API_BASE}/settings`);
  if (!res.ok) throw new Error('Failed to fetch settings');
  return res.json();
}

export async function updateSettings(settings: Partial<Settings>): Promise<Settings> {
  const res = await fetch(`${API_BASE}/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  if (!res.ok) throw new Error('Failed to update settings');
  return res.json();
}

export async function getAIStatus(): Promise<AIStatus> {
  const res = await fetch(`${API_BASE}/status`);
  if (!res.ok) throw new Error('Failed to fetch AI status');
  return res.json();
}

export async function getDiagnostics(): Promise<Diagnostics> {
  const res = await fetch(`${API_BASE}/diagnostics`);
  if (!res.ok) throw new Error('Failed to fetch diagnostics');
  return res.json();
}

async function opsRequest(path: string, method: 'GET' | 'POST' = 'GET') {
  const res = await fetch(`${API_BASE}${path}`, method === 'POST' ? { method, headers: { 'Content-Type': 'application/json' }, body: '{}' } : undefined);
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'This request could not be completed.');
  }
  return res.json();
}

export const getHealth = (): Promise<Health> => opsRequest('/health');
export const getStorage = (): Promise<StorageReport> => opsRequest('/storage');
export const cleanupStorage = (): Promise<unknown> => opsRequest('/storage/cleanup', 'POST');
export const startProductionTest = (): Promise<ProductionTestState> => opsRequest('/diagnostics/production-test', 'POST');
export const getProductionTest = (): Promise<ProductionTestState> => opsRequest('/diagnostics/production-test');
export const getClipGates = (clipId: string): Promise<ClipGates> => opsRequest(`/clips/${encodeURIComponent(clipId)}/gates`);
export async function recordRights(projectId: string, payload: { basis: string; note: string; evidence_url?: string; creator?: string }): Promise<unknown> {
  const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/rights`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'The rights statement could not be saved.');
  }
  return res.json();
}

async function businessRequest(path: string, method: 'GET' | 'POST' | 'DELETE' = 'GET', payload?: unknown) {
  const res = await fetch(`${API_BASE}/business${path}`, method === 'GET' ? undefined : {
    method, headers: { 'Content-Type': 'application/json' }, body: payload === undefined ? '{}' : JSON.stringify(payload) });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'This request could not be completed.');
  }
  return res.json();
}
export const getBusinessDashboard = (): Promise<any> => businessRequest('/dashboard');
export const getLedger = (): Promise<{ entries: any[] }> => businessRequest('/ledger');
export const addLedgerEntry = (entry: { kind: string; category: string; amount: number; note: string; occurred_on?: string }) => businessRequest('/ledger', 'POST', entry);
export const deleteLedgerEntry = (id: number) => businessRequest(`/ledger/${id}`, 'DELETE');
export const updateBusinessConfig = (config: Record<string, unknown>) => businessRequest('/config', 'POST', config);
export const getAffiliates = (): Promise<{ affiliates: any[] }> => businessRequest('/affiliates');
export const addAffiliate = (payload: { brand: string; url: string; categories: string[]; disclosure: string }) => businessRequest('/affiliates', 'POST', payload);
export const deleteAffiliate = (id: number) => businessRequest(`/affiliates/${id}`, 'DELETE');
export const refreshInsights = (): Promise<any> => businessRequest('/insights/refresh', 'POST');
export const collectAnalytics = (): Promise<any> => businessRequest('/analytics/collect', 'POST');

export interface TopicSuggestion { topic: string; pillar: string; breadth: 'broad' | 'narrow' }
export interface TopicAnalysis { topic: string; breadth: 'broad' | 'medium' | 'narrow' | 'unknown'; reasons: string[]; broader: string | null }
export async function getRankingTopics(breadth: 'all' | 'broad' | 'narrow' = 'all'): Promise<TopicSuggestion[]> {
  const res = await fetch(`${API_BASE}/ranking/topics?breadth=${breadth}`);
  if (!res.ok || !(res.headers.get('content-type') || '').includes('json'))
    throw new Error('Topic suggestions need the latest ClipRank backend. Stop the app and start it again with ./run_app.sh.');
  return (await res.json()).topics;
}
export async function analyzeTopic(topic: string): Promise<TopicAnalysis> {
  const res = await fetch(`${API_BASE}/ranking/topics/analyze?topic=${encodeURIComponent(topic)}`);
  if (!res.ok || !(res.headers.get('content-type') || '').includes('json')) throw new Error('Could not analyze the topic.');
  return res.json();
}

async function autoShortsRequest(path: string, payload?: unknown) {
  const res = await fetch(`${API_BASE}/auto-shorts${path}`, payload === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  if (!res.ok || !(res.headers.get('content-type') || '').includes('json')) {
    const error = await res.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'Auto Shorts needs the latest ClipRank backend. Stop the app and start it again with ./run_app.sh.');
  }
  return res.json();
}
export const startAutoShorts = (payload: { count: number; platforms: string[]; niche: string; topic?: string; voice?: string; mode?: string }) => autoShortsRequest('', payload);
export const getAutoShorts = (): Promise<any> => autoShortsRequest('/status');
export const cancelAutoShorts = (): Promise<any> => autoShortsRequest('/cancel', {});
