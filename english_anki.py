#!/usr/bin/env python3
import base64
import datetime as dt
import fcntl
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


BUNDLE_ID = "com.dustandlight.english-sentence-mining"
TOOL_DIR = Path(__file__).resolve().parent
DATA_DIR = Path.home() / "Library/Application Support/Alfred/Workflow Data" / BUNDLE_ID
CACHE_FILE = DATA_DIR / "current_sentence.json"
PENDING_FILE = DATA_DIR / "pending_cards.jsonl"
EVENT_LOG = DATA_DIR / "events.jsonl"
LOOKUP_CACHE_FILE = DATA_DIR / "lookup_cache.json"
CACHE_LOCK_FILE = DATA_DIR / "lookup_cache.lock"
INFLIGHT_DIR = DATA_DIR / "inflight"
CONFIG_FILE = TOOL_DIR / "config.json"
ANKI_URL = "http://127.0.0.1:8765"

DEFAULT_CONFIG = {
    "deck_name": "English::Sentence Mining",
    "model_name": "English Sentence Mining",
    "llm_api_key": "",
    "llm_base_url": "https://api.groq.com/openai/v1",
    "llm_model": "openai/gpt-oss-120b",
    "llm_timeout_seconds": 25,
    "preview_translation": True,
    "preview_target_lookup": True,
    "target_lookup_prefix_min_chars": 2,
    "target_lookup_prefix_max_candidates": 3,
    "public_fallback": True,
    "open_browser_after_add": True,
}

FIELDS = [
    "Card ID",
    "Word",
    "Original Sentence",
    "Cloze Sentence",
    "Word Meaning",
    "Meaning In Context",
    "Sentence Translation",
    "Part of Speech",
    "Source",
    "Created At",
    "Lookup Note",
]

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "his", "i", "in",
    "is", "it", "its", "of", "on", "or", "she", "that", "the", "their",
    "them", "they", "this", "to", "was", "were", "with", "you", "your",
}

WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)?")


class UserFacingError(Exception):
    pass


def load_config():
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            config.update(json.load(f))
    for key in DEFAULT_CONFIG:
        env_key = "ESM_" + key.upper()
        if os.environ.get(env_key):
            config[key] = coerce_config_value(key, os.environ[env_key], DEFAULT_CONFIG[key])
        if os.environ.get(key):
            config[key] = coerce_config_value(key, os.environ[key], DEFAULT_CONFIG[key])
    if os.environ.get("GROQ_API_KEY"):
        config["llm_api_key"] = os.environ["GROQ_API_KEY"]
    if os.environ.get("GROQ_MODEL"):
        config["llm_model"] = os.environ["GROQ_MODEL"]
    if os.environ.get("GROQ_BASE_URL"):
        config["llm_base_url"] = os.environ["GROQ_BASE_URL"]
    if not config.get("llm_api_key") and os.environ.get("OPENAI_API_KEY"):
        config["llm_api_key"] = os.environ["OPENAI_API_KEY"]
    if not config.get("llm_model") and os.environ.get("OPENAI_MODEL"):
        config["llm_model"] = os.environ["OPENAI_MODEL"]
    if os.environ.get("OPENAI_BASE_URL"):
        config["llm_base_url"] = os.environ["OPENAI_BASE_URL"]
    return config


def coerce_config_value(key, value, default):
    if isinstance(default, bool):
        return parse_bool(value)
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return value


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def parse_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).lower() not in {"0", "false", "no", "off"}


def log_event(event, **fields):
    ensure_data_dir()
    payload = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "event": event,
        **fields,
    }
    with EVENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def compact_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def truncate_text(text, limit=120):
    text = compact_text(text)
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 3)] + "..."


def alfred_json(items, rerun=None):
    payload = {"items": items}
    if rerun is not None:
        payload["rerun"] = rerun
    print(json.dumps(payload, ensure_ascii=False))


def notify(title, message):
    title = (title or "English Sentence Mining").replace('"', '\\"')
    message = (message or "").replace('"', '\\"')
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["/usr/bin/osascript", "-e", script], check=False)


def open_alfred(query):
    script = f'tell application id "com.runningwithcrayons.Alfred" to search {json.dumps(query)}'
    subprocess.run(["/usr/bin/osascript", "-e", script], check=False)


def applescript_string(text):
    escaped = str(text).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def load_lookup_cache():
    if not LOOKUP_CACHE_FILE.exists():
        return {}
    try:
        with LOOKUP_CACHE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_lookup_cache(cache):
    ensure_data_dir()
    items = list(cache.items())[-500:]
    tmp_file = LOOKUP_CACHE_FILE.with_name(f"{LOOKUP_CACHE_FILE.name}.{os.getpid()}.tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(dict(items), f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, LOOKUP_CACHE_FILE)


def lookup_cache_key(kind, sentence, target_text="", target_type=""):
    payload = {
        "v": 2,
        "kind": kind,
        "sentence": compact_text(sentence),
        "target_text": compact_text(target_text).lower(),
        "target_type": target_type or "",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def inflight_marker(kind, sentence, target_text="", target_type=""):
    return INFLIGHT_DIR / (lookup_cache_key(kind, sentence, target_text, target_type) + ".lock")


def is_recent_marker(marker, seconds=60):
    try:
        return marker.exists() and time.time() - marker.stat().st_mtime < seconds
    except OSError:
        return False


def get_cached_lookup(kind, sentence, target_text="", target_type=""):
    cache = load_lookup_cache()
    entry = cache.get(lookup_cache_key(kind, sentence, target_text, target_type))
    if not isinstance(entry, dict):
        return None
    return entry.get("value")


def set_cached_lookup(kind, sentence, value, target_text="", target_type=""):
    ensure_data_dir()
    with CACHE_LOCK_FILE.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            cache = load_lookup_cache()
            cache[lookup_cache_key(kind, sentence, target_text, target_type)] = {
                "ts": dt.datetime.now().isoformat(timespec="seconds"),
                "value": value,
            }
            save_lookup_cache(cache)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def start_sentence_prefetch(sentence):
    ensure_data_dir()
    INFLIGHT_DIR.mkdir(parents=True, exist_ok=True)
    marker = inflight_marker("sentence_translation", sentence)
    if is_recent_marker(marker):
        return False
    marker.write_text(dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    payload = encode_payload({"sentence": sentence})
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "prefetch-sentence", payload],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
            env=os.environ.copy(),
        )
    except Exception as exc:
        marker.unlink(missing_ok=True)
        log_event("sentence_prefetch_start_failed", sentence=sentence, error=str(exc))
        return False
    return True


def start_target_prefetch(sentence, target_text, target_type):
    ensure_data_dir()
    INFLIGHT_DIR.mkdir(parents=True, exist_ok=True)
    marker = inflight_marker("target_lookup", sentence, target_text, target_type)
    if is_recent_marker(marker):
        return False
    marker.write_text(dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    payload = encode_payload({
        "sentence": sentence,
        "target_text": target_text,
        "target_type": target_type,
    })
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "prefetch-target", payload],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
            env=os.environ.copy(),
        )
    except Exception as exc:
        marker.unlink(missing_ok=True)
        log_event("target_prefetch_start_failed", target_text=target_text, target_type=target_type, sentence=sentence, error=str(exc))
        return False
    return True


def clean_token(raw):
    token = raw.strip("'’")
    if not token:
        return ""
    key = token.lower()
    if key.endswith("'s") or key.endswith("’s"):
        token = token[:-2]
    return token


def sentence_tokens(sentence):
    return [token for token in (clean_token(match.group(0)) for match in WORD_PATTERN.finditer(sentence)) if token]


def extract_words(sentence):
    seen = set()
    words = []
    for raw in sentence_tokens(sentence):
        key = raw.lower()
        if len(key) <= 1 or key in seen or key in STOP_WORDS:
            continue
        seen.add(key)
        words.append(raw)
    return words


def extract_phrases(sentence, min_words=2, max_words=4, limit=80):
    tokens = sentence_tokens(sentence)
    seen = set()
    phrases = []
    for size in range(min_words, max_words + 1):
        if size > len(tokens):
            break
        for start in range(0, len(tokens) - size + 1):
            pieces = tokens[start:start + size]
            lower_pieces = [piece.lower() for piece in pieces]
            if all(piece in STOP_WORDS for piece in lower_pieces):
                continue
            phrase = " ".join(pieces)
            key = phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            phrases.append(phrase)
            if len(phrases) >= limit:
                return phrases
    return phrases


def extract_targets(sentence):
    targets = [{"type": "word", "text": word} for word in extract_words(sentence)]
    word_keys = {target["text"].lower() for target in targets}
    for phrase in extract_phrases(sentence):
        if phrase.lower() in word_keys:
            continue
        targets.append({"type": "phrase", "text": phrase})
    return targets


def infer_target_type(target_text):
    return "phrase" if len(sentence_tokens(target_text)) >= 2 else "word"


def build_target_payload(target_text, sentence, source, target_type=None):
    target_type = target_type or infer_target_type(target_text)
    return {
        "action": "add",
        "target_text": target_text,
        "target_type": target_type,
        "word": target_text,
        "sentence": sentence,
        "source": source,
    }


def encode_payload(payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_payload(raw):
    try:
        return json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise UserFacingError("Alfred payload 解析失败") from exc


def save_current_sentence(sentence, source="Alfred selection"):
    sentence = compact_text(sentence)
    if not sentence:
        raise UserFacingError("没有读到英文句子")
    ensure_data_dir()
    payload = {
        "sentence": sentence,
        "source": source,
        "captured_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    with CACHE_FILE.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def load_current_sentence():
    if not CACHE_FILE.exists():
        return None
    with CACHE_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def looks_like_sentence_input(query):
    if query.startswith("/"):
        return False
    words = WORD_PATTERN.findall(query)
    return len(words) >= 2 and any(ch.isspace() for ch in query)


def command_capture(argv):
    sentence = argv[0] if argv else sys.stdin.read()
    source = argv[1] if len(argv) > 1 else "Alfred selection"
    save_current_sentence(sentence, source)
    log_event("capture", source=source, sentence=compact_text(sentence))
    open_alfred("ew ")


def lookup_preview_text(lookup):
    if not lookup:
        return ""
    meaning = lookup.get("meaning_in_context_zh") or lookup.get("word_meaning_zh") or ""
    translation = lookup.get("sentence_translation_zh") or ""
    pieces = [piece for piece in (meaning, translation) if piece]
    return truncate_text(" · ".join(pieces), 140)


def maybe_target_preview(config, target_text, sentence, target_type, allow_lookup):
    if not config.get("preview_target_lookup", True):
        return "", False
    cached = get_cached_lookup("target_lookup", sentence, target_text, target_type)
    if cached:
        return lookup_preview_text(cached), False
    if not allow_lookup:
        return "", False
    start_target_prefetch(sentence, target_text, target_type)
    return "释义生成中...", True


def config_int(config, key, default):
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def should_allow_target_lookup(config, query, target_text, target_type):
    query_key = compact_text(query).lower()
    target_key = compact_text(target_text).lower()
    if not query_key or not target_key:
        return False
    if target_key == query_key:
        return True
    if target_type != "word":
        return False
    min_chars = config_int(config, "target_lookup_prefix_min_chars", 2)
    return min_chars > 0 and len(query_key) >= min_chars and target_key.startswith(query_key)


def is_prefix_lookup(query, target_text, target_type):
    query_key = compact_text(query).lower()
    target_key = compact_text(target_text).lower()
    return bool(query_key and target_type == "word" and target_key.startswith(query_key) and target_key != query_key)


def translation_item(config, sentence, snippet):
    if not config.get("preview_translation", True):
        return {
            "uid": hashlib.sha1(("translation-disabled\0" + sentence).encode("utf-8")).hexdigest(),
            "title": "当前句子已缓存",
            "subtitle": snippet,
            "valid": False,
        }, False
    translation = get_cached_lookup("sentence_translation", sentence)
    if translation:
        return {
            "uid": hashlib.sha1(("translation\0" + sentence).encode("utf-8")).hexdigest(),
            "title": "整句翻译：" + truncate_text(translation, 90),
            "subtitle": snippet,
            "valid": False,
        }, False
    start_sentence_prefetch(sentence)
    return {
        "uid": hashlib.sha1(("translation-empty\0" + sentence).encode("utf-8")).hexdigest(),
        "title": "整句翻译生成中...",
        "subtitle": "后台生成中；如无结果请检查 Configure Workflow 里的 LLM API 配置",
        "valid": False,
    }, True


def command_list(argv):
    config = load_config()
    query = compact_text(argv[0] if argv else "")
    cached = load_current_sentence()
    sentence = cached["sentence"] if cached else ""
    source = cached.get("source", "Alfred") if cached else "Alfred"

    if looks_like_sentence_input(query):
        cached = save_current_sentence(query, "Alfred keyword")
        sentence = cached["sentence"]
        source = cached["source"]
        query = ""
        log_event("keyword_sentence", sentence=sentence)

    if not sentence:
        alfred_json([
            {
                "title": "先选中一句英文，再执行 Universal Action 或热键",
                "subtitle": "也可以直接输入：ew This is an example sentence.",
                "valid": False,
            },
            {
                "title": "刷新待写入 Anki 的队列",
                "subtitle": "Anki 没打开时会暂存卡片；按 Enter 重试写入",
                "arg": encode_payload({"action": "flush"}),
                "valid": True,
            },
        ])
        return

    snippet = sentence if len(sentence) <= 110 else sentence[:107] + "..."
    top_item, translation_pending = translation_item(config, sentence, snippet)
    target_pending = False

    if query.startswith("/"):
        target_text = compact_text(query[1:])
        items = [top_item]
        if target_text:
            target_type = infer_target_type(target_text)
            preview, pending = maybe_target_preview(config, target_text, sentence, target_type, allow_lookup=True)
            target_pending = target_pending or pending
            items.append({
                "uid": hashlib.sha1((target_type + "\0" + target_text.lower() + "\0" + sentence).encode("utf-8")).hexdigest(),
                "title": target_text,
                "subtitle": f"{'词组' if target_type == 'phrase' else '单词'} · {preview or snippet}",
                "arg": encode_payload(build_target_payload(target_text, sentence, source, target_type)),
                "valid": True,
            })
        else:
            items.append({
                "title": "输入 / 后接你要查询的词组",
                "subtitle": "例如：/in light of",
                "valid": False,
            })
        items.append({
            "title": "刷新待写入 Anki 的队列",
            "subtitle": f"当前句子：{snippet}",
            "arg": encode_payload({"action": "flush"}),
            "valid": True,
        })
        log_event("list_custom", query=query, sentence=sentence, shown=1 if target_text else 0)
        alfred_json(items, rerun=1.0 if translation_pending or target_pending else None)
        return

    targets = extract_targets(sentence)
    filtered = [target for target in targets if not query or query.lower() in target["text"].lower()]
    log_event("list", query=query, sentence=sentence, candidates=len(targets), shown=len(filtered))

    items = [top_item]
    prefix_lookup_count = 0
    max_prefix_lookups = max(0, config_int(config, "target_lookup_prefix_max_candidates", 3))
    for target in filtered:
        target_text = target["text"]
        target_type = target["type"]
        allow_lookup = should_allow_target_lookup(config, query, target_text, target_type)
        if allow_lookup and is_prefix_lookup(query, target_text, target_type):
            if prefix_lookup_count >= max_prefix_lookups:
                allow_lookup = False
            else:
                prefix_lookup_count += 1
        preview, pending = maybe_target_preview(config, target_text, sentence, target_type, allow_lookup=allow_lookup)
        target_pending = target_pending or pending
        items.append({
            "uid": hashlib.sha1((target_type + "\0" + target_text.lower() + "\0" + sentence).encode("utf-8")).hexdigest(),
            "title": target_text,
            "subtitle": f"{'词组' if target_type == 'phrase' else '单词'} · {preview or snippet}",
            "arg": encode_payload(build_target_payload(target_text, sentence, source, target_type)),
            "autocomplete": target_text,
            "valid": True,
        })

    if len(items) == 1:
        items.append({
            "title": "没有匹配的候选词或词组",
            "subtitle": "删掉过滤词，输入 /词组 精确指定，或重新选一句英文",
            "valid": False,
        })

    items.append({
        "title": "刷新待写入 Anki 的队列",
        "subtitle": f"当前句子：{snippet}",
        "arg": encode_payload({"action": "flush"}),
        "valid": True,
    })
    alfred_json(items, rerun=1.0 if translation_pending or target_pending else None)


def request_json(url, payload=None, headers=None, timeout=25):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"User-Agent": "EnglishSentenceMining/0.1"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=request_headers)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def translate_public(text):
    if not text:
        return ""
    query = urllib.parse.urlencode({"q": text, "langpair": "en|zh-CN"})
    try:
        data = request_json(f"https://api.mymemory.translated.net/get?{query}", timeout=12)
        return compact_text(data.get("responseData", {}).get("translatedText", ""))
    except Exception:
        return ""


def dictionary_public(word):
    url = "https://api.dictionaryapi.dev/api/v2/entries/en/" + urllib.parse.quote(word)
    try:
        data = request_json(url, timeout=12)
    except Exception:
        return "", ""
    try:
        meanings = data[0]["meanings"]
        meaning = next((m for m in meanings if m.get("partOfSpeech") != "noun"), meanings[0])
        pos = meaning.get("partOfSpeech", "")
        definition = meaning["definitions"][0].get("definition", "")
        return compact_text(pos), compact_text(definition)
    except Exception:
        return "", ""


def extract_json_object(text):
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def llm_settings(config):
    api_key = (
        os.environ.get("GROQ_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or config.get("llm_api_key", "")
    )
    model = (
        os.environ.get("GROQ_MODEL")
        or os.environ.get("LLM_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or config.get("llm_model", "")
    )
    base_url = (
        os.environ.get("GROQ_BASE_URL")
        or os.environ.get("LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or config.get("llm_base_url", "")
    ).rstrip("/")
    return api_key, model, base_url or "https://api.groq.com/openai/v1"


def run_llm_json(config, messages, timeout=None):
    api_key, model, base_url = llm_settings(config)
    if not api_key or not model:
        return None

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    seconds = timeout if timeout is not None else int(config.get("llm_timeout_seconds") or 25)
    data = request_json(f"{base_url}/chat/completions", payload, headers=headers, timeout=seconds)
    return extract_json_object(data["choices"][0]["message"]["content"])


def test_llm_connection(config):
    api_key, model, base_url = llm_settings(config)
    if not api_key:
        raise UserFacingError("未配置 LLM API Key")
    if not model:
        raise UserFacingError("未配置 LLM Model")
    messages = [
        {"role": "system", "content": "Return valid JSON only."},
        {"role": "user", "content": json.dumps({
            "task": "connectivity_test",
            "schema": {"ok": True, "message": "short Chinese status"},
        }, ensure_ascii=False)},
    ]
    result = run_llm_json(config, messages, timeout=20)
    if not result:
        raise UserFacingError("LLM API 没有返回 JSON")
    host = urllib.parse.urlparse(base_url).netloc or base_url
    message = compact_text(str(result.get("message") or "连接正常"))
    return f"LLM 连接正常：{model} @ {host}；{message}"


def translate_sentence_llm(config, sentence):
    messages = [
        {"role": "system", "content": "Translate English to concise, natural Chinese. Return JSON only."},
        {"role": "user", "content": json.dumps({"sentence": sentence, "schema": {"sentence_translation_zh": "translation"}}, ensure_ascii=False)},
    ]
    result = run_llm_json(config, messages, timeout=25)
    if not result:
        return ""
    translation = compact_text(result.get("sentence_translation_zh", "") if result else "")
    return translation


def context_phrase(sentence, target_text):
    if infer_target_type(target_text) == "phrase":
        return target_text
    pattern = re.compile(rf"\b({re.escape(target_text)})\b\s+([A-Za-z]+)", flags=re.IGNORECASE)
    match = pattern.search(sentence)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return target_text


def cloze_sentence(sentence, target_text):
    pattern = re.compile(rf"\b({re.escape(target_text)})\b", flags=re.IGNORECASE)
    return pattern.sub(r"<b>\1</b>", html.escape(sentence), count=1)


def lookup_llm(config, target_text, sentence, target_type="word"):
    system = (
        "You create concise Chinese Anki vocabulary or phrase notes from English sentence mining. "
        "Return valid JSON only."
    )
    user = {
        "target_text": target_text,
        "target_type": target_type,
        "sentence": sentence,
        "schema": {
            "word": "target surface form; may be a word or a phrase",
            "lemma": "dictionary form for a word, or normalized phrase",
            "part_of_speech": "part of speech in this sentence",
            "word_meaning_zh": "common Chinese meaning for the target, concise",
            "meaning_in_context_zh": "meaning in this exact sentence, concise",
            "sentence_translation_zh": "full Chinese sentence translation",
            "cloze_sentence": "same sentence, with the exact target wrapped in <b>...</b>",
        },
    }
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]
    result = run_llm_json(config, messages, timeout=35)
    if not result:
        return None
    result.setdefault("word", target_text)
    result.setdefault("lemma", target_text.lower())
    result.setdefault("part_of_speech", "")
    result.setdefault("word_meaning_zh", "")
    result.setdefault("meaning_in_context_zh", "")
    result.setdefault("sentence_translation_zh", "")
    result.setdefault("cloze_sentence", cloze_sentence(sentence, target_text))
    result["lookup_note"] = f"llm_api: {llm_settings(config)[1]}"
    return result


def lookup_public(config, target_text, sentence, target_type="word"):
    if not config.get("public_fallback", True):
        raise UserFacingError("没有配置 LLM API key/model，且 public_fallback=false")
    is_phrase = target_type == "phrase" or infer_target_type(target_text) == "phrase"
    sentence_zh = translate_public(sentence)

    if is_phrase:
        target_zh = translate_public(target_text)
        meaning = target_zh or "未查到词组释义"
        context_meaning = target_zh or meaning
        pos = ""
        lookup_note = "public_fallback_phrase: free translation only; configure LLM API key and model for precise phrase meanings"
    else:
        pos, definition = dictionary_public(target_text)
        definition_zh = translate_public(definition) if definition else ""
        phrase = context_phrase(sentence, target_text)
        phrase_zh = translate_public(phrase) if phrase.lower() != target_text.lower() else ""
        target_zh = translate_public(target_text)
        meaning = definition_zh or target_zh or definition or "未查到释义"
        if definition and definition_zh:
            meaning = f"{definition_zh}; {definition}"
        context_meaning = phrase_zh or definition_zh or target_zh or meaning
        lookup_note = "public_fallback: configure LLM API key and model for precise contextual meanings"

    return {
        "word": target_text,
        "lemma": target_text.lower(),
        "part_of_speech": pos,
        "word_meaning_zh": meaning,
        "meaning_in_context_zh": context_meaning,
        "sentence_translation_zh": sentence_zh,
        "cloze_sentence": cloze_sentence(sentence, target_text),
        "lookup_note": lookup_note,
    }


def sentence_translation(config, sentence):
    if not config.get("preview_translation", True):
        return ""
    cached = get_cached_lookup("sentence_translation", sentence)
    if cached:
        return cached
    errors = []
    try:
        translation = translate_sentence_llm(config, sentence)
        if translation:
            set_cached_lookup("sentence_translation", sentence, translation)
            return translation
    except Exception as exc:
        errors.append(str(exc))
        log_event("llm_sentence_failed", sentence=sentence, error=str(exc))
    if config.get("public_fallback", True):
        translation = translate_public(sentence)
        if translation:
            set_cached_lookup("sentence_translation", sentence, translation)
            return translation
    if errors:
        log_event("sentence_translation_unavailable", sentence=sentence, error=" | ".join(errors[-2:]))
    return ""


def lookup_word(config, target_text, sentence, target_type="word"):
    cached = get_cached_lookup("target_lookup", sentence, target_text, target_type)
    if cached:
        return cached
    errors = []
    try:
        result = lookup_llm(config, target_text, sentence, target_type)
        if result:
            set_cached_lookup("target_lookup", sentence, result, target_text, target_type)
            if result.get("sentence_translation_zh"):
                set_cached_lookup("sentence_translation", sentence, result["sentence_translation_zh"])
            return result
    except Exception as exc:
        if not config.get("public_fallback", True):
            reason = " | ".join(errors + [str(exc)])
            raise UserFacingError(f"查询失败：{reason}") from exc
        errors.append(str(exc))
        log_event("llm_lookup_failed", target_text=target_text, target_type=target_type, sentence=sentence, error=str(exc))
    result = lookup_public(config, target_text, sentence, target_type)
    set_cached_lookup("target_lookup", sentence, result, target_text, target_type)
    if result.get("sentence_translation_zh"):
        set_cached_lookup("sentence_translation", sentence, result["sentence_translation_zh"])
    return result


def anki_invoke(action, params=None, timeout=25):
    payload = {"action": action, "version": 6}
    if params is not None:
        payload["params"] = params
    data = request_json(ANKI_URL, payload, timeout=timeout)
    if data.get("error"):
        raise UserFacingError(str(data["error"]))
    return data.get("result")


def wait_for_anki(seconds=20):
    try:
        return anki_invoke("version", timeout=3)
    except Exception:
        subprocess.run(["/usr/bin/open", "-a", "Anki"], check=False)
    deadline = time.time() + seconds
    last_error = None
    while time.time() < deadline:
        try:
            return anki_invoke("version", timeout=3)
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise UserFacingError(f"AnkiConnect 不可用：{last_error}")


def ensure_anki_schema(config):
    wait_for_anki()
    deck = config["deck_name"]
    model = config["model_name"]

    decks = anki_invoke("deckNames")
    if deck not in decks:
        anki_invoke("createDeck", {"deck": deck})

    models = anki_invoke("modelNames")
    if model in models:
        return

    css = """
.card { font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif; font-size: 20px; line-height: 1.45; text-align: left; color: #202124; background: #ffffff; }
.word { font-size: 30px; font-weight: 700; margin-bottom: 12px; }
.sentence { color: #3c4043; margin: 12px 0; }
.label { color: #5f6368; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; margin-top: 14px; }
.value { margin-top: 4px; }
b { color: #b3261e; }
"""
    front = """
<div class="word">{{Word}}</div>
<div class="sentence">{{Cloze Sentence}}</div>
"""
    back = """
{{FrontSide}}
<hr id="answer">
<div class="label">Common Meaning</div>
<div class="value">{{Word Meaning}}</div>
<div class="label">In This Sentence</div>
<div class="value">{{Meaning In Context}}</div>
<div class="label">Sentence Translation</div>
<div class="value">{{Sentence Translation}}</div>
<div class="label">Part of Speech</div>
<div class="value">{{Part of Speech}}</div>
<div class="label">Source</div>
<div class="value">{{Source}}</div>
"""
    anki_invoke("createModel", {
        "modelName": model,
        "inOrderFields": FIELDS,
        "cardTemplates": [{"Name": "Sentence Card", "Front": front, "Back": back}],
        "css": css,
    })


def build_note(config, target_text, sentence, source, target_type=None):
    target_type = target_type or infer_target_type(target_text)
    lookup = lookup_word(config, target_text, sentence, target_type)
    card_id = hashlib.sha1((target_text.lower() + "\0" + compact_text(sentence)).encode("utf-8")).hexdigest()[:16]
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    fields = {
        "Card ID": card_id,
        "Word": html.escape(lookup.get("word") or target_text),
        "Original Sentence": html.escape(sentence),
        "Cloze Sentence": lookup.get("cloze_sentence") or cloze_sentence(sentence, target_text),
        "Word Meaning": html.escape(lookup.get("word_meaning_zh", "")),
        "Meaning In Context": html.escape(lookup.get("meaning_in_context_zh", "")),
        "Sentence Translation": html.escape(lookup.get("sentence_translation_zh", "")),
        "Part of Speech": html.escape(lookup.get("part_of_speech", "")),
        "Source": html.escape(source or "Alfred selection"),
        "Created At": now,
        "Lookup Note": html.escape(lookup.get("lookup_note", "")),
    }
    return {
        "deckName": config["deck_name"],
        "modelName": config["model_name"],
        "fields": fields,
        "options": {"allowDuplicate": False},
        "tags": ["english", "sentence-mining", "alfred", f"target-{target_type}"],
    }


def add_note(config, note):
    ensure_anki_schema(config)
    note_id = anki_invoke("addNote", {"note": note}, timeout=35)
    if config.get("open_browser_after_add", True):
        try:
            anki_invoke("guiBrowse", {"query": f"nid:{note_id}"}, timeout=5)
        except Exception as exc:
            log_event("gui_browse_failed", note_id=note_id, error=str(exc))
    return note_id


def queue_note(note):
    ensure_data_dir()
    with PENDING_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(note, ensure_ascii=False) + "\n")


def flush_pending(config):
    if not PENDING_FILE.exists():
        return "没有待写入队列"
    notes = []
    with PENDING_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                notes.append(json.loads(line))
    if not notes:
        PENDING_FILE.unlink(missing_ok=True)
        return "没有待写入队列"

    remaining = []
    added = 0
    for note in notes:
        try:
            add_note(config, note)
            added += 1
        except Exception as exc:
            if "duplicate" not in str(exc).lower():
                remaining.append(note)
    if remaining:
        with PENDING_FILE.open("w", encoding="utf-8") as f:
            for note in remaining:
                f.write(json.dumps(note, ensure_ascii=False) + "\n")
    else:
        PENDING_FILE.unlink(missing_ok=True)
    return f"已写入 {added} 张，剩余 {len(remaining)} 张"


def command_add(argv, should_notify=False):
    if not argv:
        raise UserFacingError("没有收到 Alfred 选择的目标词/词组")
    payload = decode_payload(argv[0])
    config = load_config()

    if payload.get("action") == "flush":
        message = flush_pending(config)
        if should_notify:
            notify("English Sentence Mining", message)
        print(message)
        return

    target_text = compact_text(payload.get("target_text") or payload.get("word", ""))
    target_type = payload.get("target_type") or infer_target_type(target_text)
    sentence = compact_text(payload.get("sentence", ""))
    source = compact_text(payload.get("source", "Alfred selection"))
    if not target_text or not sentence:
        raise UserFacingError("目标词/词组或原句为空")

    note = build_note(config, target_text, sentence, source, target_type)
    log_event("add_start", target_text=target_text, target_type=target_type, sentence=sentence, source=source)
    try:
        note_id = add_note(config, note)
        log_event("add_success", target_text=target_text, target_type=target_type, note_id=note_id, deck=config["deck_name"], model=config["model_name"])
        message = f"已加入 Anki：{target_text}"
    except Exception as exc:
        if "duplicate" in str(exc).lower():
            log_event("add_duplicate", target_text=target_text, target_type=target_type, sentence=sentence, error=str(exc))
            message = f"已存在：{target_text}"
        else:
            queue_note(note)
            log_event("add_queued", target_text=target_text, target_type=target_type, sentence=sentence, error=str(exc))
            message = f"Anki 暂不可用，已暂存：{target_text}"
    if should_notify:
        notify("English Sentence Mining", message)
    print(message)


def command_test(should_notify=False):
    config = load_config()
    try:
        message = test_llm_connection(config)
        log_event("llm_test_success", model=llm_settings(config)[1], base_url=llm_settings(config)[2])
    except UserFacingError:
        raise
    except Exception as exc:
        log_event("llm_test_failed", error=str(exc))
        raise UserFacingError(f"LLM 连接失败：{truncate_text(str(exc), 220)}") from exc
    if should_notify:
        notify("English Sentence Mining", message)
    print(message)


def command_prefetch_sentence(argv):
    if not argv:
        return
    payload = decode_payload(argv[0])
    sentence = compact_text(payload.get("sentence", ""))
    marker = inflight_marker("sentence_translation", sentence)
    try:
        if sentence:
            sentence_translation(load_config(), sentence)
    finally:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass


def command_prefetch_target(argv):
    if not argv:
        return
    payload = decode_payload(argv[0])
    sentence = compact_text(payload.get("sentence", ""))
    target_text = compact_text(payload.get("target_text", ""))
    target_type = payload.get("target_type") or infer_target_type(target_text)
    marker = inflight_marker("target_lookup", sentence, target_text, target_type)
    try:
        if sentence and target_text:
            lookup_word(load_config(), target_text, sentence, target_type)
    finally:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "list"
    argv = sys.argv[2:]
    try:
        if command == "capture":
            command_capture(argv)
        elif command == "list":
            command_list(argv)
        elif command == "add":
            command_add(argv, should_notify=False)
        elif command == "add-notify":
            command_add(argv, should_notify=True)
        elif command == "flush-notify":
            config = load_config()
            message = flush_pending(config)
            notify("English Sentence Mining", message)
            print(message)
        elif command == "test":
            command_test(should_notify=False)
        elif command == "test-notify":
            command_test(should_notify=True)
        elif command == "prefetch-sentence":
            command_prefetch_sentence(argv)
        elif command == "prefetch-target":
            command_prefetch_target(argv)
        else:
            raise UserFacingError(f"未知命令：{command}")
    except UserFacingError as exc:
        if command == "list":
            alfred_json([{"title": str(exc), "valid": False}])
        else:
            notify("English Sentence Mining", str(exc))
            print(str(exc), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
