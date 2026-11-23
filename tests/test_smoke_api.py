"""接口冒烟测试：路由、鉴权、任务提交与查询。

覆盖 /、/health、/v1/audio/transcriptions-asyn/{submit,query} 的
主干路径与边界（401/400/404），不依赖真实 FunASR 模型。
"""

import json
import uuid

from app.core.config import settings

SUBMIT_URL = "/v1/audio/transcriptions-asyn/submit"
QUERY_URL = "/v1/audio/transcriptions-asyn/query"


def _submit_form(**overrides) -> dict:
    data = {
        "file_url": "https://example.com/smoke.wav",
        "model": "funasr-standard",
        "response_format": "json",
        "language": "zh",
    }
    data.update(overrides)
    return data


# ---------- 无鉴权端点 ----------


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == settings.PROJECT_NAME
    assert body["docs"] == "/docs"


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["model"] == settings.FUNASR_MODEL
    assert body["device"] == settings.FUNASR_DEVICE


# ---------- 鉴权边界 ----------


def test_submit_missing_header_returns_422(client):
    """未携带 Authorization 头：FastAPI Header 必填校验先于自定义校验，返回 422。"""
    resp = client.post(SUBMIT_URL, data=_submit_form())
    assert resp.status_code == 422


def test_submit_wrong_scheme_returns_401(client):
    resp = client.post(
        SUBMIT_URL, data=_submit_form(), headers={"Authorization": "Basic abc"}
    )
    assert resp.status_code == 401


def test_submit_wrong_key_returns_401(client):
    resp = client.post(
        SUBMIT_URL, data=_submit_form(), headers={"Authorization": "Bearer wrong"}
    )
    assert resp.status_code == 401


def test_query_requires_auth(client):
    resp = client.get(QUERY_URL, params={"task_id": str(uuid.uuid4())})
    assert resp.status_code in (401, 422)


# ---------- 提交与查询主干 ----------


def test_submit_creates_pending_task(client, auth_headers):
    resp = client.post(SUBMIT_URL, data=_submit_form(), headers=auth_headers)
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    # 返回合法 UUID，可被 query 接口接受
    uuid.UUID(task_id)

    query = client.get(QUERY_URL, params={"task_id": task_id}, headers=auth_headers)
    assert query.status_code == 200
    assert query.json()["task_status"] == "pending"


def test_query_invalid_task_id_returns_400(client, auth_headers):
    resp = client.get(
        QUERY_URL, params={"task_id": "not-a-uuid"}, headers=auth_headers
    )
    assert resp.status_code == 400
    assert "Invalid task_id" in resp.json()["detail"]


def test_query_unknown_task_returns_404(client, auth_headers):
    resp = client.get(
        QUERY_URL, params={"task_id": str(uuid.uuid4())}, headers=auth_headers
    )
    assert resp.status_code == 404


def test_query_completed_task_returns_result(client, auth_headers, seed_task):
    task_id = seed_task(
        status="completed",
        result=json.dumps(
            {
                "task_id": "placeholder",
                "task_status": "completed",
                "text": "你好世界",
                "language": "zh",
                "duration": 1.5,
                "segments": [
                    {
                        "id": 0,
                        "start": 0.0,
                        "end": 1.5,
                        "text": "你好世界",
                        "speaker": "Speaker 1",
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    resp = client.get(
        QUERY_URL, params={"task_id": str(task_id)}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_status"] == "completed"
    assert body["text"] == "你好世界"
    assert body["duration"] == 1.5
    assert body["segments"][0]["speaker"] == "Speaker 1"


def test_query_failed_task_returns_error(client, auth_headers, seed_task):
    task_id = seed_task(
        status="failed",
        error="download failed",
        result=json.dumps(
            {
                "task_id": "placeholder",
                "task_status": "failed",
                "error": "download failed",
            },
            ensure_ascii=False,
        ),
    )
    resp = client.get(
        QUERY_URL, params={"task_id": str(task_id)}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_status"] == "failed"
    assert body["error"] == "download failed"
