import os
import json
import time
import uuid
import asyncio
import logging
import threading
from datetime import datetime
from typing import Optional

from sqlalchemy import select, and_

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.asr_task import AsrTask
from app.services.funasr_runtime import transcribe_local
from app.services.file_utils import download_file, remove_file

logger = logging.getLogger(__name__)

_task_semaphore = threading.Semaphore(settings.MAX_CONCURRENT_TASKS)
_worker_thread: Optional[threading.Thread] = None
_worker_running = False


def _run_async(coro):
    """Run an async coroutine in a new event loop (used by the worker thread)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        except Exception:
            pass


async def _pick_pending_task() -> Optional[str]:
    """Atomically pick one pending task and mark it as processing."""
    async with AsyncSessionLocal() as session:
        stmt = (
            select(AsrTask)
            .where(
                and_(
                    AsrTask.status == "pending",
                    AsrTask.retry_count < AsrTask.max_retries,
                )
            )
            .order_by(AsrTask.created_at.asc())
            .limit(1)
        )
        result = await session.execute(stmt)
        task = result.scalars().first()
        if task is None:
            return None
        task.status = "processing"
        task.updated_at = datetime.utcnow()
        await session.commit()
        return str(task.task_id)


async def _process_task(task_id: str) -> None:
    """Download -> ASR -> persist result; retry or fail based on retry_count."""
    task_id = uuid.UUID(task_id) if isinstance(task_id, str) else task_id
    async with AsyncSessionLocal() as session:
        stmt = select(AsrTask).where(AsrTask.task_id == task_id)
        result = await session.execute(stmt)
        task = result.scalars().first()
        if task is None:
            logger.warning(f"[worker] Task not found: {task_id}")
            return
        task.retry_count += 1
        task.status = "processing"
        task.updated_at = datetime.utcnow()
        await session.commit()
        file_url = task.file_url
        language = task.language or ""
        task_uuid = str(task.task_id)

    file_path: Optional[str] = None
    try:
        # 1) Download
        file_path = await asyncio.to_thread(download_file, file_url, task_uuid)

        # Persist file path for observability/debugging
        async with AsyncSessionLocal() as session:
            stmt = select(AsrTask).where(AsrTask.task_id == task_id)
            result = await session.execute(stmt)
            t = result.scalars().first()
            if t:
                t.file_path = file_path
                await session.commit()

        # 2) ASR (CPU/GPU heavy -> thread)
        data = await asyncio.to_thread(transcribe_local, file_path)

        # 3) Build result payload
        segments = []
        total_duration = 0.0
        for i, s in enumerate(data.get("sentence_info", [])):
            start_sec = (s.get("start", 0) or 0) / 1000.0
            end_sec = (s.get("end", 0) or 0) / 1000.0
            total_duration += max(0.0, end_sec - start_sec)
            spk_idx = int(s.get("spk", 0)) + 1
            segments.append({
                "id": i,
                "start": start_sec,
                "end": end_sec,
                "text": s.get("text", ""),
                "speaker": f"Speaker {spk_idx}",
            })

        result_data = {
            "task_id": task_uuid,
            "task_status": "completed",
            "text": data.get("text", ""),
            "language": language,
            "duration": total_duration,
            "segments": segments,
        }

        # 4) Mark completed
        async with AsyncSessionLocal() as session:
            stmt = select(AsrTask).where(AsrTask.task_id == task_id)
            result = await session.execute(stmt)
            t = result.scalars().first()
            if t:
                t.status = "completed"
                t.result = json.dumps(result_data, ensure_ascii=False)
                t.completed_at = datetime.utcnow()
                await session.commit()
        logger.info(f"[worker] Task completed: {task_id}")

    except Exception as e:
        logger.error(f"[worker] Task {task_id} failed: {e}", exc_info=True)
        error_msg = str(e)
        async with AsyncSessionLocal() as session:
            stmt = select(AsrTask).where(AsrTask.task_id == task_id)
            result = await session.execute(stmt)
            t = result.scalars().first()
            if t:
                if t.retry_count >= t.max_retries:
                    t.status = "failed"
                    t.error = error_msg
                    t.completed_at = datetime.utcnow()
                    t.result = json.dumps({
                        "task_id": task_uuid,
                        "task_status": "failed",
                        "error": error_msg,
                    }, ensure_ascii=False)
                else:
                    t.status = "pending"
                    t.error = error_msg
                t.updated_at = datetime.utcnow()
                await session.commit()
    finally:
        # Always clean up the local audio file
        await asyncio.to_thread(remove_file, file_path)


async def _reset_interrupted_tasks() -> None:
    """Reset tasks left in 'processing' state from a previous crash back to 'pending'."""
    async with AsyncSessionLocal() as session:
        stmt = select(AsrTask).where(AsrTask.status == "processing")
        result = await session.execute(stmt)
        tasks = result.scalars().all()
        if not tasks:
            return
        reset_count = 0
        failed_count = 0
        for task in tasks:
            if task.retry_count >= task.max_retries:
                task.status = "failed"
                task.error = "Interrupted by service restart; retry limit exhausted"
                failed_count += 1
            else:
                task.status = "pending"
                reset_count += 1
            task.updated_at = datetime.utcnow()
        await session.commit()
        logger.info(f"[init] Reset {reset_count} interrupted task(s) to pending, {failed_count} marked failed")


def _worker_loop() -> None:
    """Background polling loop with semaphore-based concurrency control."""
    global _worker_running
    logger.info("[worker] started")

    # Reset tasks interrupted by a previous crash (own event loop in this thread)
    try:
        _run_async(_reset_interrupted_tasks())
    except Exception as e:
        logger.error(f"[init] failed to reset interrupted tasks: {e}", exc_info=True)

    while _worker_running:
        try:
            task_id = _run_async(_pick_pending_task())
            if task_id is None:
                time.sleep(settings.POLL_INTERVAL_SEC)
                continue

            # Block until a slot is available (queueing)
            if not _task_semaphore.acquire(timeout=5):
                # Put the task back to pending if we couldn't claim a slot quickly
                # Note: it is already marked processing; reset to pending for next round
                _run_async(_reset_one_processing(task_id))
                continue

            try:
                logger.info(f"[worker] processing task {task_id}")
                _run_async(_process_task(task_id))
            finally:
                _task_semaphore.release()
        except Exception as e:
            logger.error(f"[worker] loop error: {e}", exc_info=True)
            time.sleep(settings.POLL_INTERVAL_SEC)
    logger.info("[worker] stopped")


async def _reset_one_processing(task_id: str) -> None:
    """Roll back a single task from processing -> pending."""
    task_id = uuid.UUID(task_id) if isinstance(task_id, str) else task_id
    async with AsyncSessionLocal() as session:
        stmt = select(AsrTask).where(AsrTask.task_id == task_id)
        result = await session.execute(stmt)
        t = result.scalars().first()
        if t and t.status == "processing":
            t.status = "pending"
            t.updated_at = datetime.utcnow()
            await session.commit()


def start_worker() -> None:
    """Start the background worker thread (idempotent)."""
    global _worker_thread, _worker_running

    # Start worker thread (it resets interrupted tasks on entry)
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_running = True
        _worker_thread = threading.Thread(
            target=_worker_loop,
            daemon=True,
            name="asr-worker",
        )
        _worker_thread.start()
        logger.info("[init] ASR worker thread started")


def stop_worker() -> None:
    """Signal the worker thread to exit."""
    global _worker_running
    _worker_running = False
