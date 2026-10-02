import { Project, Job, Settings, AIStatus, Diagnostics } from './types';

const API_BASE = '/api';

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
  layout?: 'fill' | 'fit';
  segment_duration?: number;
  voice?: string;
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

export async function regenerateProject(projectId: string): Promise<{ job_id: string }> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/regenerate`, { method: 'POST' });
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
