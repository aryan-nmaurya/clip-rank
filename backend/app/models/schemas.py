from typing import Literal, Optional, List, Dict, Any
from pydantic import BaseModel, Field

ProjectMode = Literal["viral", "ranking"]

JobStatus = Literal[
    "QUEUED",
    "INGESTING",
    "TRANSCRIBING",
    "ANALYZING",
    "DETECTING_MOMENTS",
    "DEDUPLICATING",
    "RANKING",
    "SCRIPTING",
    "GENERATING_VOICE",
    "EDITING",
    "RENDERING",
    "CLEANING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
]

class ViralCreateRequest(BaseModel):
    video_url: Optional[str] = None
    count: Optional[int] = 3
    ai_provider: Optional[str] = "auto"
    target_duration: Optional[str] = "Auto"
    caption_style: Optional[str] = "bold_yellow"
    hook_generation: Optional[bool] = True
    aspect_ratio: Optional[str] = "9:16"

class RankingCreateRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=180)
    count: Optional[int] = Field(default=None, ge=3, le=10)
    ai_provider: Literal["auto", "gemini", "openai", "local"] = "auto"
    source_urls: List[str] = Field(default_factory=list, max_length=20)
    narration: bool = False
    layout: Literal["fill", "fit"] = "fill"
    segment_duration: float = Field(default=7, ge=3, le=12)
    voice: str = "Samantha"

class CandidateMoment(BaseModel):
    id: str
    source_id: str
    start: float
    end: float
    description: str
    topic_relevance: float = 0.9
    visual_impact: float = 0.85
    surprise: float = 0.8
    clarity: float = 0.9
    emotion: float = 0.8
    uniqueness: float = 0.85
    retention: float = 0.88
    overall_score: float = 0.85

class OutputClip(BaseModel):
    id: str
    project_id: str
    job_id: str
    title: str
    subtitle: Optional[str] = None
    duration: float = 0.0
    rank: Optional[int] = None
    viral_score: Optional[int] = None
    reason: Optional[str] = None
    status: str = "PROCESSING"  # PROCESSING | READY | FAILED
    preview_path: Optional[str] = None
    video_path: Optional[str] = None
    created_at: Optional[str] = None

class JobResponse(BaseModel):
    id: str
    project_id: str
    status: str
    progress: int = 0
    current_stage: str = "Queued"
    error_message: Optional[str] = None
    detailed_error: Optional[str] = None
    created_at: str
    updated_at: str

class ProjectResponse(BaseModel):
    id: str
    mode: ProjectMode
    title: str
    status: str
    input_data: Dict[str, Any] = {}
    result_data: Dict[str, Any] = {}
    clips: List[OutputClip] = []
    job: Optional[JobResponse] = None
    created_at: str
    updated_at: str

class SettingsModel(BaseModel):
    ai_provider: str = "auto"
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    local_endpoint: str = "http://localhost:11434"
    local_model: str = "qwen2.5:latest"
    default_voice: str = "Samantha"
    language: str = "en"
    hardware_accel: str = "cpu"
    temp_retention_hours: int = 12

class AIStatusResponse(BaseModel):
    status_text: str
    provider: str
    is_ready: bool
    local_available: bool
    gemini_configured: bool
    openai_configured: bool
    active_model: str
    message: Optional[str] = None

class DiagnosticsResponse(BaseModel):
    ai_provider: str
    ffmpeg_available: bool
    ffmpeg_version: Optional[str] = None
    hw_accel: str
    gemini_configured: bool
    openai_configured: bool
    local_endpoint_reachable: bool
    total_projects: int
    completed_projects: int
    system_load: Optional[str] = "Optimal"
