import json
import uuid
import logging
from typing import List

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy import select

from app.core.security import verify_bearer
from app.db.session import AsyncSessionLocal
from app.models.asr_task import AsrTask
from app.schemas.asr import HealthResponse, SubmitResponse, TaskResult
from app.services.funasr_runtime import get_status

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check: returns FunASR model availability."""
    return HealthResponse(**get_status())


@router.post(
    "/v1/audio/transcriptions-asyn/submit",
    response_model=SubmitResponse,
    status_code=status.HTTP_200_OK,
)
async def submit_transcription(
    file_url: str = Form(..., description="Audio file download URL"),
    model: str = Form(..., description="Model name, e.g. funasr-standard"),
    response_format: str = Form(
        "json",
        description="json | verbose_json | text",
    ),
    language: str = Form("", description="Language code, e.g. zh, en"),
    timestamp_granularities: List[str] = Form(
        [],
        description="Currently only 'segment' is supported",
    ),
    _token: str = Depends(verify_bearer),
) -> SubmitResponse:
    """Submit an asynchronous ASR task."""
    task_id = uuid.uuid4()
    logger.info(
        f"[submit] task_id={task_id} file_url={file_url} "
        f"model={model} response_format={response_format} language={language}"
    )
    try:
        async with AsyncSessionLocal() as session:
            task = AsrTask(
                task_id=task_id,
                file_url=file_url,
                model=model,
                response_format=response_format or "json",
                language=language or "",
                status="pending",
                retry_count=0,
                max_retries=2,
            )
            session.add(task)
            await session.commit()
        logger.info(f"[submit] task created: {task_id}")
    except Exception as e:
        logger.error(f"[submit] failed to create task: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit task",
        )
    return SubmitResponse(task_id=str(task_id))


@router.get(
    "/v1/audio/transcriptions-asyn/query",
    response_model=TaskResult,
)
async def query_transcription(
    task_id: str,
    _token: str = Depends(verify_bearer),
) -> TaskResult:
    """Query the status / result of an asynchronous ASR task."""
    logger.info(f"[query] task_id={task_id}")
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid task_id format",
        )

    async with AsyncSessionLocal() as session:
        stmt = select(AsrTask).where(AsrTask.task_id == task_uuid)
        result = await session.execute(stmt)
        task = result.scalars().first()

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    if task.status == "completed":
        if task.result:
            try:
                data = json.loads(task.result)
                return TaskResult(**data)
            except Exception:
                logger.exception("[query] failed to parse stored result")
                return TaskResult(task_id=task_id, task_status="completed")
        return TaskResult(task_id=task_id, task_status="completed")

    if task.status == "failed":
        resp = {"task_id": task_id, "task_status": "failed"}
        if task.error:
            resp["error"] = task.error
        if task.result:
            try:
                data = json.loads(task.result)
                resp.update({k: v for k, v in data.items() if k not in resp})
            except Exception:
                pass
        return TaskResult(**resp)

    # pending / processing
    return TaskResult(task_id=task_id, task_status=task.status)
