"""冒烟测试夹具。

关键设计：
- 环境变量覆盖必须在导入任何 app 模块**之前**完成（Settings 在导入时实例化），
  因此放在模块顶部而非 fixture 中。
- DATABASE_URI 指向临时 sqlite 文件，避免污染仓库内的 asr_service.db。
- start_worker / stop_worker 替换为空操作，避免起后台 worker 线程轮询任务；
  /health 的 get_status 被 mock，避免真实加载 FunASR 模型（约 1.5GB）。
"""

import atexit
import os
import shutil
import tempfile

# ---- 必须先于 app 导入：Settings 在导入时读取环境变量 ----
_TMP_DIR = tempfile.mkdtemp(prefix="asr_smoke_")
atexit.register(shutil.rmtree, _TMP_DIR, ignore_errors=True)
os.environ["DATABASE_URI"] = f"sqlite+aiosqlite:///{_TMP_DIR}/smoke.db"
os.environ["ASR_API_KEY"] = "smoke-test-key"
os.environ["UPLOAD_DIR"] = os.path.join(_TMP_DIR, "uploads")

import uuid  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.models.asr_task import AsrTask  # noqa: E402


def _fake_status() -> dict:
    """替代 funasr_runtime.get_status，不触发模型加载。"""
    return {
        "available": True,
        "model": settings.FUNASR_MODEL,
        "device": settings.FUNASR_DEVICE,
        "hotword_supported": True,
    }


@pytest.fixture
def client(monkeypatch):
    """带 lifespan 的同步测试客户端（init_db 在临时库上建表）。"""
    import app.main as app_main

    monkeypatch.setattr(app_main, "start_worker", lambda: None)
    monkeypatch.setattr(app_main, "stop_worker", lambda: None)
    monkeypatch.setattr("app.api.v1.endpoints.asr.get_status", _fake_status)
    with TestClient(app_main.app) as c:
        yield c


@pytest.fixture
def auth_headers() -> dict:
    return {"Authorization": f"Bearer {settings.ASR_API_KEY}"}


@pytest.fixture
def seed_task():
    """返回一个直接向临时库写入 AsrTask 记录的函数（绕过 worker 链路）。

    使用独立引擎 + 独立事件循环，避免与 TestClient 的事件循环复用连接。
    """

    def _seed(**overrides) -> uuid.UUID:
        import asyncio

        from sqlalchemy import insert
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        values = {
            "task_id": uuid.uuid4(),
            "file_url": "https://example.com/smoke.wav",
            "model": "funasr-standard",
            "response_format": "json",
            "language": "zh",
            "hotword": None,
            "status": "pending",
            "retry_count": 0,
            "max_retries": 2,
        }
        values.update(overrides)

        async def _run() -> None:
            engine = create_async_engine(
                settings.DATABASE_URI, poolclass=NullPool
            )
            try:
                async with engine.begin() as conn:
                    await conn.execute(insert(AsrTask).values(**values))
            finally:
                await engine.dispose()

        asyncio.run(_run())
        return values["task_id"]

    return _seed
