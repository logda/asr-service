"""全链路冒烟测试（仅 make smoke 触发，默认 pytest 运行会排除）。

与 mock 冒烟的关键差异：不打任何补丁——真实加载 FunASR 模型、
真实启动 worker 线程，验证 提交 -> 下载 -> 转写 -> 落库 -> 查询 整条链路。

依赖本地已下载模型（make models）；未下载时整体跳过而非失败。
数据库沿用 conftest 的临时 sqlite，不触碰仓库内的 asr_service.db。
断言刻意宽松（不断言精确文本），避免模型版本漂移导致脆断。
"""

import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

EXAMPLE_DIR = os.path.join(
    "models",
    "iic",
    "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "example",
)
EXAMPLE_WAV = os.path.join(EXAMPLE_DIR, "asr_example.wav")

# 300s 截止时间：覆盖首次模型加载（约 1 分钟）+ worker 轮询间隔
DEADLINE_SEC = 300
POLL_INTERVAL_SEC = 2

pytestmark = [
    pytest.mark.smoke_full,
    pytest.mark.skipif(
        not os.path.exists(EXAMPLE_WAV),
        reason="本地模型未下载（先执行 make models），跳过全链路冒烟",
    ),
]


@pytest.fixture
def real_client():
    """无补丁的测试客户端：lifespan 真实执行 init_db + start_worker。"""
    from fastapi.testclient import TestClient

    import app.main as app_main

    with TestClient(app_main.app) as c:
        yield c


@pytest.fixture
def file_server():
    """随机端口静态文件服务器，把示例 wav 变成 worker 可下载的 URL。"""
    handler = partial(SimpleHTTPRequestHandler, directory=EXAMPLE_DIR)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


def test_health_real_model(real_client):
    """真实加载 FunASR 模型：顺带验证本地模型文件可用。"""
    resp = real_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["available"] is True


def test_full_chain_submit_to_completed(
    real_client, file_server, auth_headers
):
    submit_url = "/v1/audio/transcriptions-asyn/submit"
    query_url = "/v1/audio/transcriptions-asyn/query"

    resp = real_client.post(
        submit_url,
        data={
            "file_url": f"{file_server}/asr_example.wav",
            "model": "funasr-standard",
            "language": "zh",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    deadline = time.monotonic() + DEADLINE_SEC
    body = {}
    while time.monotonic() < deadline:
        query = real_client.get(
            query_url, params={"task_id": task_id}, headers=auth_headers
        )
        assert query.status_code == 200
        body = query.json()
        if body["task_status"] in ("completed", "failed"):
            break
        time.sleep(POLL_INTERVAL_SEC)

    assert body.get("task_status") == "completed", body
    assert str(body["task_id"]) == task_id
    assert body["text"].strip()
    assert body["segments"]
    assert body["segments"][0]["speaker"].startswith("Speaker")
    assert body["duration"] > 0
