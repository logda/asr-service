from pydantic_settings import BaseSettings
import os


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
    FUNASR_MODEL: str = "paraformer-zh"
    FUNASR_VAD_MODEL: str = "fsmn-vad"
    FUNASR_PUNC_MODEL: str = "ct-punc"
    FUNASR_SPK_MODEL: str = "cam++"
    FUNASR_MODEL_REVISION: str = "v2.0.4"
    FUNASR_VAD_MODEL_REVISION: str = "v2.0.4"
    FUNASR_PUNC_MODEL_REVISION: str = "v2.0.4"
    FUNASR_SPK_MODEL_REVISION: str = "v2.0.2"

    MODELS_DIR: str = os.path.abspath("./models")

    UPLOAD_DIR: str = "./uploads/asr"

    DOWNLOAD_TIMEOUT_SEC: int = 30

    LOG_LEVEL: str = "INFO"

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "ignore"


settings = Settings()
