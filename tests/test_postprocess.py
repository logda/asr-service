"""FunASR 结果后处理纯函数冒烟测试（构造输入，不加载模型）。

覆盖 _normalize_result / _post_process_sentences / _normalize_hotword
的代表性路径，防止后处理逻辑被改坏而接口层测试无法察觉。
"""

from app.services.funasr_runtime import (
    _normalize_hotword,
    _normalize_result,
    _post_process_sentences,
)


def test_normalize_result_basic():
    res = [
        {
            "key": "asr_example",
            "text": "你好世界。",
            "timestamp": [[0, 200]],
            "sentence_info": [
                {
                    "text": "你好。",
                    "start": 0,
                    "end": 100,
                    "timestamp": [[0, 50], [50, 100]],
                    "spk": 0,
                },
                {
                    "text": "世界。",
                    "start": 100,
                    "end": 200,
                    "timestamp": [[100, 150], [150, 200]],
                    "spk": 0,
                },
            ],
        }
    ]
    out = _normalize_result("audio/asr_example.wav", res)
    assert out["key"] == "asr_example"
    # 两段无重叠/无缝合词，正文保留模型输出
    assert out["text"] == "你好世界。"
    assert len(out["sentence_info"]) == 2
    assert out["timestamp"] == [[0, 50], [50, 100], [100, 150], [150, 200]]


def test_post_process_filters_punct_only_segments():
    segs = [
        {"text": "，。", "start": 0, "end": 50, "timestamp": [], "spk": 0},
        {
            "text": "你好",
            "start": 50,
            "end": 150,
            "timestamp": [[50, 100], [100, 150]],
            "spk": 0,
        },
    ]
    out = _post_process_sentences(segs)
    assert [s["text"] for s in out] == ["你好"]


def test_normalize_hotword_filters_urls_and_dedup():
    # URL 形态热词防 SSRF 一律丢弃；重复词去重保序
    assert (
        _normalize_hotword("招商银行 招商银行 https://evil.example/hw.txt")
        == "招商银行"
    )
    assert _normalize_hotword("") is None
    assert _normalize_hotword(None) is None
