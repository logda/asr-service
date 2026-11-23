import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    # FunASR 1.4.x 新增的 surface 切句会对带标点全文做 jieba 分词，
    # 与字级时间戳对齐失败时标点会插进词中间（"可。以"）。
    # 置 None 后回退到字级旧逻辑 timestamp_sentence（与 1.3.x 行为一致）。
    import funasr.auto.auto_model as _funasr_auto
    _funasr_auto._timestamp_sentences_from_surface = lambda *a, **k: None
except Exception:  # funasr 未安装或版本无此符号时不影响服务启动
    pass

_model: Optional[Any] = None
_funasr_available: bool = True

# 句段后处理：纯标点判定与尾部标点剥离用的字符集
_PUNCT_CHARS = (
    r"\s，。、；：！？…—·～“”‘’（）()《》〈〉【】「」『』"
    r"\[\]{},.!?;:~`\"'\-_/\\|"
)
_PUNCT_ONLY_RE = re.compile("^[" + _PUNCT_CHARS + "]+$")
_TRAILING_PUNCT_RE = re.compile("[" + _PUNCT_CHARS + "]+$")
# 问句、感叹句和闭合引号/括号属于语义边界，不按词内错位标点删除。
_PROTECTED_BOUNDARY_RE = re.compile(r"[！？!?“”‘’\"'（）()\[\]{}《》〈〉【】「」『』]")
# 前段去标点后不超过该字数才视为 VAD 重叠切出的碎片并参与合并
_MERGE_MAX_PREV_CHARS = 6
# FunASR 原始时间单位为毫秒；API 层才转换为秒。
_CLOSE_GAP_MS = 500
_MAX_OVERLAP_MS = 500
_DIGIT_CHARS = set("零一二三四五六七八九两0123456789")
# 常用双字词兜底；避免 jieba 缺失或金融场景新词不在词典时漏判。
_BOUNDARY_WORDS = frozenset({"关于", "可以", "微信", "每个", "程序", "比如", "当场", "首绑"})
_JIEBA = None
# 热词允许的分隔符：空白、中英文逗号、顿号、分号、竖线
_HOTWORD_SPLIT_RE = re.compile(r"[\s,，、;；|]+")
# FunASR 的 generate_hotwords_list 对 http 开头的热词串会服务端主动 GET 下载，
# 形成 SSRF 向量；本地模式仅接受纯文本热词，URL 形态的 token 一律丢弃。
_HOTWORD_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _resolve_local_model_dir(models_dir: str, model_name: str) -> str:
    try:
        from funasr.download.name_maps_from_hub import name_maps_ms
        mapped = name_maps_ms.get(model_name, model_name)
    except Exception:
        mapped = model_name
    candidates = [
        os.path.join(models_dir, mapped),
        os.path.join(models_dir, "models", mapped),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return os.path.join(models_dir, mapped)


def _init_model():
    global _model
    global _funasr_available
    if _model is not None:
        return _model
    try:
        os.makedirs(settings.MODELS_DIR, exist_ok=True)
        models_dir = os.path.abspath(settings.MODELS_DIR)
        cache_root = os.path.dirname(models_dir.rstrip(os.sep)) or os.sep
        os.environ.setdefault("MODELSCOPE_CACHE", cache_root)
        os.environ.setdefault("HUGGINGFACE_HUB_CACHE", cache_root)
        os.environ.setdefault("HF_HOME", cache_root)

        from funasr import AutoModel

        main_dir = _resolve_local_model_dir(models_dir, settings.FUNASR_MODEL)
        vad_dir = _resolve_local_model_dir(models_dir, settings.FUNASR_VAD_MODEL)
        punc_dir = _resolve_local_model_dir(models_dir, settings.FUNASR_PUNC_MODEL)
        spk_dir = (
            _resolve_local_model_dir(models_dir, settings.FUNASR_SPK_MODEL)
            if settings.FUNASR_SPK_MODEL
            else None
        )

        _model = AutoModel(
            model=main_dir,
            model_revision=settings.FUNASR_MODEL_REVISION,
            vad_model=vad_dir,
            vad_model_revision=settings.FUNASR_VAD_MODEL_REVISION,
            punc_model=punc_dir,
            punc_model_revision=settings.FUNASR_PUNC_MODEL_REVISION,
            spk_model=spk_dir,
            spk_model_revision=settings.FUNASR_SPK_MODEL_REVISION,
            device=settings.FUNASR_DEVICE,
            disable_update=True,
            check_latest=False,
            vad_kwargs={"silence_schedule": [(float("inf"), 800)]},
        )
        _funasr_available = True
        logger.info(
            f"FunASR model initialized: model={settings.FUNASR_MODEL}, "
            f"vad={settings.FUNASR_VAD_MODEL}, punc={settings.FUNASR_PUNC_MODEL}, "
            f"spk={settings.FUNASR_SPK_MODEL}, device={settings.FUNASR_DEVICE}"
        )
    except Exception:
        logger.error("Failed to initialize FunASR model", exc_info=True)
        _funasr_available = False
        _model = None
    return _model


def _normalize_hotword(raw: Optional[str]) -> Optional[str]:
    """清洗热词串：多分隔符切分、去重保序、按词数与字数控断。

    SeACo 的偏置编码长度有限，超量热词既拖慢解码又稀释相关词权重，
    因此必须硬性截断，不能原样透传。
    """
    if not raw:
        return None
    words: List[str] = []
    seen = set()
    total = 0
    for word in _HOTWORD_SPLIT_RE.split(raw.strip()):
        if not word or word in seen:
            continue
        if _HOTWORD_URL_RE.match(word):
            logger.warning("[hotword] 已丢弃 URL 形态的热词，防 SSRF")
            continue
        if len(words) >= settings.MAX_HOTWORD_WORDS:
            logger.warning(f"热词超过 {settings.MAX_HOTWORD_WORDS} 个，已截断")
            break
        if total + len(word) > settings.MAX_HOTWORD_CHARS:
            logger.warning(f"热词总字数超过 {settings.MAX_HOTWORD_CHARS}，已截断")
            break
        seen.add(word)
        words.append(word)
        total += len(word)
    return " ".join(words) or None


def _resolve_hotword(raw: Optional[str]) -> Optional[str]:
    """请求热词优先；为空时回退到全局默认词表。"""
    if not settings.HOTWORD_ENABLED:
        return None
    return _normalize_hotword(raw) or _normalize_hotword(settings.DEFAULT_HOTWORDS)


def _hotword_supported() -> bool:
    """只有 SeACo-Paraformer 带偏置解码器；普通 Paraformer 会静默忽略 hotword。"""
    inner = getattr(_model, "model", None)
    name = type(inner).__name__ if inner is not None else ""
    return "seaco" in name.lower() or "seaco" in settings.FUNASR_MODEL.lower()


def _strip_trailing_punct(text: str) -> str:
    return _TRAILING_PUNCT_RE.sub("", text or "")


def _overlap_len(prev: str, nxt: str) -> int:
    """prev 后缀与 nxt 前缀的最长重叠长度，用于去除 VAD 段边界重复识别字。"""
    for size in range(min(len(prev), len(nxt)), 0, -1):
        if prev[-size:] == nxt[:size]:
            return size
    return 0


def _duplicate_overlap_len(prev: Dict[str, Any], nxt: Dict[str, Any]) -> int:
    """只有字级时间区间充分重合，才把边界相同字视为重复识别。"""
    left = _strip_trailing_punct(prev["text"])
    right = _strip_trailing_punct(nxt["text"])
    left_ts = prev.get("timestamp") or []
    right_ts = nxt.get("timestamp") or []
    # 英文词级时间戳、合并后粒度不明等场景一律保留，不猜测字与时间戳对齐。
    if len(left_ts) != len(left) or len(right_ts) != len(right):
        return 0
    size = _overlap_len(left, right)
    for count in range(size, 0, -1):
        if left[-count:] != right[:count]:
            continue
        matched = True
        for a, b in zip(left_ts[-count:], right_ts[:count]):
            overlap = min(a[1], b[1]) - max(a[0], b[0])
            longest = max(a[1] - a[0], b[1] - b[0])
            if longest <= 0 or overlap <= 0 or overlap * 2 < longest:
                matched = False
                break
        if matched:
            return count
    return 0


def _jieba_cut(text: str) -> List[str]:
    global _JIEBA
    if _JIEBA is None:
        try:
            import jieba
        except ImportError:
            _JIEBA = False
            logger.warning("jieba 不可用，接缝修复仅使用常用词和数字规则")
        else:
            _JIEBA = jieba.Tokenizer()
    if _JIEBA is False:
        return list(text)
    return list(_JIEBA.cut(text, HMM=False))


def _is_mid_word_junction(prev_clean: str, nxt: str) -> bool:
    """用数字连读或跨接缝词判断疑似词中断句，不把短段一律合并。"""
    if not prev_clean or not nxt:
        return False
    if prev_clean[-1] in _DIGIT_CHARS and nxt[0] in _DIGIT_CHARS:
        return True
    if prev_clean[-1] + nxt[0] in _BOUNDARY_WORDS:
        return True
    k = min(8, len(prev_clean))
    window = prev_clean[-k:] + nxt[:8]
    pos = 0
    for tok in _jieba_cut(window):
        start, end = pos, pos + len(tok)
        if start < k < end and 2 <= len(tok) <= 4 and all("\u4e00" <= c <= "\u9fff" for c in tok):
            return True
        pos = end
    return False


def _post_process_sentences(sentence_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """处理纯标点段、短重叠碎片及疑似词中切句，时间单位均为毫秒。

    同说话人且接缝构成词时合并；非重叠段不删除重复字。
    跨说话人不合并、不重写标签；只去掉常用词接缝的疑似错位标点，
    保留原时间边界。词典只能提供文本启发式证据，不能证明说话人分簇错误。
    """
    merged: List[Dict[str, Any]] = []
    for seg in sentence_info or []:
        text = str(seg.get("text") or "")
        if not text.strip() or _PUNCT_ONLY_RE.fullmatch(text):
            continue
        seg = dict(seg)
        seg["text"] = text
        if merged:
            prev = merged[-1]
            prev_stripped = prev["text"].rstrip()
            prev_clean = _strip_trailing_punct(prev_stripped)
            gap = float(seg.get("start") or 0) - float(prev.get("end") or 0)
            same_spk = seg.get("spk") == prev.get("spk")
            close = -_MAX_OVERLAP_MS <= gap <= _CLOSE_GAP_MS
            trailing = prev_stripped[len(prev_clean):]
            protected = bool(_PROTECTED_BOUNDARY_RE.search(trailing))
            has_punct = bool(prev_clean) and bool(trailing) and not protected
            duplicate = (
                _duplicate_overlap_len(prev, seg)
                if close and gap < 0 and same_spk and not protected and len(prev_clean) <= _MERGE_MAX_PREV_CHARS
                else 0
            )
            mid_word = close and has_punct and _is_mid_word_junction(prev_clean, text)
            if same_spk and (duplicate or mid_word):
                prev["text"] = prev_clean + text[duplicate:]
                prev["end"] = max(int(prev.get("end") or 0), int(seg.get("end") or 0))
                # 保留参与合并段的原始区间；时间戳不再保证与修复后的字一一对应。
                prev["timestamp"] = list(prev.get("timestamp") or []) + list(seg.get("timestamp") or [])
                continue
            if (
                not same_spk and close and has_punct
                and prev_clean[-1] + text[0] in _BOUNDARY_WORDS
            ):
                prev["text"] = prev_clean
        merged.append(seg)
    return merged


def _preserve_full_text_whitespace(original: str, repaired: str) -> str:
    """把模型全文中未修改区域的空白带回修复正文，避免英文词粘连。"""
    if not original or not any(c.isspace() for c in original):
        return repaired
    original_positions = [i for i, c in enumerate(original) if not c.isspace()]
    left = "".join(original[i] for i in original_positions)
    right = "".join(c for c in repaired if not c.isspace())
    matching = SequenceMatcher(None, left, right, autojunk=False)
    mapped = {}
    for block in matching.get_matching_blocks():
        for offset in range(block.size):
            mapped[block.b + offset] = block.a + offset
    result: List[str] = []
    index = 0
    for char in repaired:
        if not char.isspace():
            current = mapped.get(index)
            previous = mapped.get(index - 1)
            if current is not None and (not result or not result[-1][-1].isspace()):
                if index == 0 and current == 0:
                    gap = original[:original_positions[current]]
                elif previous is not None and current == previous + 1:
                    gap = original[original_positions[previous] + 1:original_positions[current]]
                else:
                    gap = ""
                if gap:
                    result.append(gap)
            index += 1
        result.append(char)
    if right and result and not result[-1][-1].isspace() and mapped.get(len(right) - 1) == len(left) - 1:
        result.append(original[original_positions[-1] + 1:])
    return "".join(result)


def _normalize_result(file_path: str, res: Any) -> Dict[str, Any]:
    key = os.path.splitext(os.path.basename(file_path))[0]
    text = ""
    timestamp: List[List[int]] = []
    sentence_info: List[Dict[str, Any]] = []

    obj = res
    if isinstance(obj, list) and obj:
        obj = obj[0]

    if isinstance(obj, dict):
        text = obj.get("text") or obj.get("raw_text") or ""
        si = obj.get("sentence_info") or []
        if isinstance(si, list) and si:
            for item in si:
                st = int(item.get("start", 0))
                ed = int(item.get("end", st))
                ts = item.get("timestamp") or [[st, ed]]
                sentence_info.append({
                    "text": item.get("text", ""),
                    "start": st,
                    "end": ed,
                    "timestamp": ts,
                    "spk": int(item.get("spk", 0)),
                })
                timestamp.extend([[int(a), int(b)] for a, b in ts])
        else:
            tw = obj.get("timestamp") or []
            if isinstance(tw, list):
                timestamp = [[int(a), int(b)] for a, b in tw]
            sentence_info.append({
                "text": text,
                "start": timestamp[0][0] if timestamp else 0,
                "end": timestamp[-1][1] if timestamp else 0,
                "timestamp": timestamp or [],
                "spk": 0,
            })
    else:
        text = str(obj)
        sentence_info.append({
            "text": text,
            "start": 0,
            "end": 0,
            "timestamp": [],
            "spk": 0,
        })

    before_text = "".join(str(s.get("text") or "") for s in sentence_info)
    sentence_info = _post_process_sentences(sentence_info)
    after_text = "".join(s["text"] for s in sentence_info)
    # 有文本修复时同步正文；未修复则保留模型全文的空格等排版。
    if after_text != before_text or not text:
        text = _preserve_full_text_whitespace(text, after_text)
    # 顶层时间戳对应保留下来的段；纯标点过滤段不再进入输出。
    timestamp = [
        [int(a), int(b)]
        for s in sentence_info
        for a, b in (s.get("timestamp") or [])
    ]

    return {
        "key": key,
        "text": text,
        "timestamp": timestamp,
        "sentence_info": sentence_info,
    }


def transcribe_local(file_path: str, hotword: Optional[str] = None) -> Dict[str, Any]:
    m = _init_model()
    if not (_funasr_available and m is not None):
        raise RuntimeError("FunASR model unavailable")
    normalized = _resolve_hotword(hotword)
    if normalized and not _hotword_supported():
        logger.warning(
            f"[hotword] 当前模型 {settings.FUNASR_MODEL} 不支持热词，已忽略；"
            "需切换到 SeACo-Paraformer"
        )
        normalized = None
    if normalized:
        logger.info(f"[hotword] applied {len(normalized.split())} word(s)")
    # 显式传 hotword（包括 None）：AutoModel 会把 cfg 合入 self.kwargs，
    # 不传则可能残留上一次请求的热词。SeACo 对 None 走普通解码路径。
    res = m.generate(input=file_path, hotword=normalized)
    return _normalize_result(file_path, res)


def get_status() -> Dict[str, Any]:
    m = _init_model()
    return {
        "available": bool(_funasr_available and m is not None),
        "model": settings.FUNASR_MODEL,
        "device": settings.FUNASR_DEVICE,
        "hotword_supported": _hotword_supported(),
    }
