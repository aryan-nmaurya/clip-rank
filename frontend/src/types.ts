export type ProjectMode = 'viral' | 'ranking' | 'discovery' | 'movie';
export type AIProviderType = 'auto' | 'gemini' | 'openai' | 'local' | 'groq' | 'nvidia';

export interface Settings {
  ai_provider: AIProviderType;
  gemini_api_key?: string;
  gemini_api_key_configured?: boolean;
  gemini_api_key_2?: string;
  gemini_fallback_models?: string | null;
  gemini_api_key_2_configured?: boolean;
  gemini_model: string;
  openai_api_key?: string;
  openai_api_key_configured?: boolean;
  openai_model: string;
  groq_api_key?: string;
  groq_api_key_configured?: boolean;
  groq_model?: string | null;
  nvidia_api_key?: string;
  nvidia_api_key_configured?: boolean;
  nvidia_model?: string | null;
  local_endpoint: string;
  local_model: string;
  default_voice: string;
  language: string;
  hardware_accel: string;
  temp_retention_hours: number;
  watermark_enabled: boolean;
  enforce_publish_gates?: boolean;
  quality_control?: boolean;
  watermark_text: string;
}

export interface AIStatus {
  status_text: string;
  provider: AIProviderType;
  is_ready: boolean;
  ranking_ready: boolean;
  local_available: boolean;
  gemini_configured: boolean;
  openai_configured: boolean;
  groq_configured?: boolean;
  nvidia_configured?: boolean;
  active_model: string;
  message?: string;
}

export interface Diagnostics {
  ai_provider: string;
  ffmpeg_available: boolean;
  ffmpeg_version?: string;
  hw_accel: string;
  gemini_configured: boolean;
  openai_configured: boolean;
  local_endpoint_reachable: boolean;
  total_projects: number;
  completed_projects: number;
  system_load?: string;
}

export interface OutputClip {
  id: string;
  project_id: string;
  job_id: string;
  title: string;
  subtitle?: string;
  duration: number;
  rank?: number;
  viral_score?: number;
  reason?: string;
  status: 'PROCESSING' | 'READY' | 'FAILED';
  preview_path?: string;
  video_path?: string;
  created_at?: string;
}

export interface YouTubeConnection {
  configured: boolean;
  connected: boolean;
  channel_title?: string;
  channel_id?: string;
  default_privacy: 'private' | 'unlisted' | 'public';
  default_made_for_kids: boolean;
}

export interface YouTubeUpload {
  clip_id: string;
  status: 'QUEUED' | 'UPLOADING' | 'UPLOADED' | 'FAILED' | 'DELETED';
  progress: number;
  metadata: { title: string; description: string; privacy: string; made_for_kids: boolean; tags?:string[];scene_description?:string };
  error?: string;
  url?: string;
  actual_privacy?: string;
  video_id?: string;
}

export interface JobFailure {
  stage: string;
  stage_detail?: string;
  what: string;
  why: string;
  retryable: boolean;
  next_step: string;
  code: string;
}

export interface HealthCheck { id: string; label: string; required: boolean; ok: boolean; detail: string; fix: string }
export interface Health { ready: boolean; status: string; problems: { id: string; detail: string; fix: string }[]; checks: HealthCheck[] }

export interface StorageReport {
  bytes: Record<string, number>; total_bytes: number; cache_bytes: number; cache_budget_bytes: number;
  cache_over_budget: boolean; disk_free_bytes: number; disk_low: boolean; temp_job_dirs: number;
}

export interface ProductionTestStep { name: string; status: 'pending' | 'running' | 'passed' | 'failed' | 'skipped'; detail?: string; seconds?: number }
export interface ProductionTestState {
  status: 'idle' | 'running' | 'done'; ready?: boolean; verdict?: string; failed_component?: string | null;
  steps?: ProductionTestStep[]; output?: string | null;
}

export interface Gate { passed: boolean; reason: string; status?: string; verified?: boolean }
export interface ClipGates { clip_id: string; passed: boolean; enforced?: boolean; blockers: string[]; gates: { technical: Gate; rights: Gate; originality: Gate } }

export interface Job {
  id: string;
  project_id: string;
  status: string;
  progress: number;
  current_stage: string;
  error_message?: string;
  detailed_error?: string;
  failure?: JobFailure | null;
  created_at: string;
  updated_at: string;
}

export interface Project {
  id: string;
  mode: ProjectMode;
  title: string;
  status: string;
  input_data: Record<string, any>;
  result_data: Record<string, any>;
  clips: OutputClip[];
  job?: Job;
  created_at: string;
  updated_at: string;
}
