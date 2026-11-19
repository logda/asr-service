from typing import List, Optional
from pydantic import BaseModel, Field


class SubmitResponse(BaseModel):
    task_id: str = Field(..., description="ASR task ID")


class SegmentItem(BaseModel):
    id: int
    start: float
    end: float
    text: str
    speaker: str


class TaskResult(BaseModel):
    task_id: str
    task_status: str
    text: Optional[str] = None
    language: Optional[str] = None
    duration: Optional[float] = None
    segments: Optional[List[SegmentItem]] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    available: bool
    model: str
    device: str
