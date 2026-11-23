import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    # force=True：import 链（funasr/modelscope）会先给 root logger 挂 handler，
    # 不加 force 时 basicConfig 会静默失效，导致应用 INFO 日志丢失、LOG_LEVEL 配置无效。
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
