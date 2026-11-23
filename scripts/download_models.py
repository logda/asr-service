"""按服务期望的目录布局 ./models/iic/<model_id> 预置 FunASR 模型。

服务 _resolve_local_model_dir 只认 MODELS_DIR/iic/<model_id>，
funasr 对不存在的本地路径会当作模型 ID 远端请求（404），不会自动下载，
因此本地开发需先运行本脚本预置模型（生产环境为运行时挂载，无需本步骤）。

用法: .venv/bin/python scripts/download_models.py
"""
import sys
from pathlib import Path

MODELS_ROOT = Path(__file__).resolve().parent.parent / "models"

MODELS = [
    ("iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch", "v2.0.4"),
    ("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch", "v2.0.4"),
    ("iic/punc_ct-transformer_cn-en-common-vocab471067-large", "v2.0.4"),
    ("iic/speech_campplus_sv_zh-cn_16k-common", "v2.0.2"),
]


def main() -> int:
    from modelscope.hub.snapshot_download import snapshot_download

    for mid, rev in MODELS:
        target = MODELS_ROOT / mid
        if (target / "model.pt").exists() or (target / "configuration.json").exists():
            print(f"[skip] {mid} 已存在", flush=True)
            continue
        print(f"=== downloading {mid} @ {rev} ===", flush=True)
        snapshot_download(model_id=mid, revision=rev, local_dir=str(target))
        print(f"=== done {mid} ===", flush=True)
    print("ALL DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
