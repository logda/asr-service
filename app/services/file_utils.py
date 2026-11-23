import logging
import os
from typing import Optional
from urllib.parse import urlparse

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


def download_file(file_url: str, task_uuid: str) -> str:
    """
    Download file from URL to local upload directory.
    Returns the local file path.
    """
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    parsed_url = urlparse(file_url)
    filename = os.path.basename(parsed_url.path) or "audio.wav"
    file_ext = os.path.splitext(filename)[1] or ".wav"
    unique_filename = f"{task_uuid}{file_ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)

    logger.info(f"[download] task={task_uuid} url={file_url} -> {file_path}")
    resp = requests.get(file_url, stream=True, timeout=settings.DOWNLOAD_TIMEOUT_SEC)
    resp.raise_for_status()
    with open(file_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    logger.info(f"[download] task={task_uuid} done. size={os.path.getsize(file_path)}")
    return file_path


def remove_file(file_path: Optional[str]) -> None:
    if not file_path:
        return
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"[cleanup] removed {file_path}")
    except Exception as e:
        logger.warning(f"[cleanup] failed to remove {file_path}: {e}")
