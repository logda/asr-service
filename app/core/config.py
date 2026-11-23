import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "ASR-Service"
    API_V1_STR: str = ""

    AUTH: str = "Authorization"

    ASR_API_KEY: str = "sk-your-key"

    HOST: str = "0.0.0.0"
    PORT: int = 8001

    DATABASE_URI: str = "sqlite+aiosqlite:///./asr_service.db"

    MAX_CONCURRENT_TASKS: int = 1
    MAX_RETRIES: int = 2
    POLL_INTERVAL_SEC: int = 2

    FUNASR_DEVICE: str = "cpu"
    # 热词（hotword）基于 SeACo-Paraformer 的上下文偏置能力，普通 paraformer-zh
    # 的 inference 不接受 hotword 入参。需要无热词基线时改回 "paraformer-zh" 即可。
    # 必须带 modelscope 命名空间前缀 iic/：无前缀 ID 既不在 name_maps_ms 别名表，
    # 也无法被远端 snapshot_download 解析，本地缓存目录同样按 iic/<id> 存放。
    FUNASR_MODEL: str = (
        "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
    )
    FUNASR_VAD_MODEL: str = "fsmn-vad"
    FUNASR_PUNC_MODEL: str = "ct-punc"
    FUNASR_SPK_MODEL: str = "cam++"
    FUNASR_MODEL_REVISION: str = "v2.0.4"
    FUNASR_VAD_MODEL_REVISION: str = "v2.0.4"
    FUNASR_PUNC_MODEL_REVISION: str = "v2.0.4"
    FUNASR_SPK_MODEL_REVISION: str = "v2.0.2"

    MODELS_DIR: str = os.path.abspath("./models")

    # ===== Hotword =====
    # 总开关：关闭后既不读默认词表也不向推理传参，可快速回退
    HOTWORD_ENABLED: bool = True
    # 请求未携带热词时使用的全局兜底词表（空格/逗号/顿号分隔）
    DEFAULT_HOTWORDS: str = ""
    # SeACo 的偏置编码有长度上限，超量热词会拖慢解码并稀释相关词权重
    MAX_HOTWORD_WORDS: int = 100
    MAX_HOTWORD_CHARS: int = 2000

    UPLOAD_DIR: str = "./uploads/asr"

    DOWNLOAD_TIMEOUT_SEC: int = 30

    LOG_LEVEL: str = "INFO"

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "ignore"


settings = Settings()
