"""Обработчик вызова GigaChat API: проверка ввода, промпт, вызов модели, карта ошибок.

UI (streamlit_app.py) ничего не знает про HTTP, токены и коды ошибок — он вызывает
generate() и показывает результат. Вся работа с API — здесь.

Настройки берутся из переменных окружения (файл .env), секреты в коде не хранятся:
  GIGACHAT_CREDENTIALS  ключ авторизации из личного кабинета (обязательно)
  GIGACHAT_SCOPE        GIGACHAT_API_PERS (по умолчанию) | GIGACHAT_API_B2B | GIGACHAT_API_CORP
  GIGACHAT_MODEL        модель, по умолчанию GigaChat-2
  GIGACHAT_CA_BUNDLE    путь к сертификату «Russian Trusted Root CA» (.cer/.crt)
  GIGACHAT_TIMEOUT      таймаут ответа модели в секундах, по умолчанию 30
  GIGACHAT_AUTH_URL / GIGACHAT_BASE_URL  — адреса API (менять нужно только для тестов)
"""
import json
import logging
import os
import ssl
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import certifi
import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

# ----------------------------------------------------------------------------- настройки
AUTH_URL = os.getenv("GIGACHAT_AUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
BASE_URL = os.getenv("GIGACHAT_BASE_URL", "https://api.giga.chat/v1").rstrip("/")
SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-2")
TIMEOUT = float(os.getenv("GIGACHAT_TIMEOUT", "30"))
CONNECT_TIMEOUT = 5.0
RETRY_PAUSE_SECONDS = 1.0

PEOPLE_OPTIONS = ["1", "2", "4", "6"]
FORMAT_LIST = "Список продуктов"
FORMAT_STEPS = "Список + шаги"
FORMAT_OPTIONS = [FORMAT_LIST, FORMAT_STEPS]
MAX_DISH_LENGTH = 300  # описание блюда — это пара фраз; длиннее — почти наверняка не то

# ----------------------------------------------------------------------------- сообщения пользователю
MSG_EMPTY = "Напишите, что хотите приготовить."
MSG_TOO_LONG = "Слишком длинный текст — сократите или разбейте на части."
MSG_BAD_OPTION = "Выберите количество людей и формат из списка."
MSG_ACCESS = "Не настроен доступ к сервису."
MSG_RATE_LIMIT = "Слишком много запросов. Подождите немного и попробуйте снова."
MSG_SERVER = "Сервис временно недоступен. Попробуйте позже."
MSG_NETWORK = "Не удалось получить ответ. Попробуйте ещё раз."
MSG_EMPTY_ANSWER = "Пустой ответ модели. Попробуйте переформулировать вопрос."
MSG_UNKNOWN = "Что-то пошло не так. Попробуйте ещё раз."

# ----------------------------------------------------------------------------- логи без секретов
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
log = logging.getLogger("llm")
if not log.handlers:
    _handler = logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def _log(level: int, event: str, **fields) -> None:
    """Пишем «скелет» событий: тип, длины, коды, время. Никаких ключей, токенов и полного текста."""
    log.log(level, json.dumps({"event": event, **fields}, ensure_ascii=False))


# ----------------------------------------------------------------------------- результат для UI
@dataclass
class Result:
    ok: bool                 # True — получили ответ модели
    text: str                # ответ модели или одно предложение об ошибке
    kind: str = "ok"         # ok | validation | api_error — UI выбирает, как показать
    elapsed: float = 0.0     # сколько длился вызов модели, секунд


class AccessError(Exception):
    """Нет ключа, неверный ключ, нет прав, проблема с сертификатом — повтор не поможет."""


class RateLimitError(Exception):
    """429 — превышен лимит запросов."""


class ServerError(Exception):
    """5xx — сервис недоступен. Можно повторить один раз."""


class NetworkError(Exception):
    """Таймаут или обрыв соединения. Можно повторить один раз."""


class EmptyAnswerError(Exception):
    """Ответ пустой или не в ожидаемом формате."""


# ----------------------------------------------------------------------------- проверка ввода
def validate(dish: str, people: str, fmt: str) -> Optional[str]:
    """Возвращает текст ошибки или None. Вызывается ДО обращения к API."""
    if not dish or not dish.strip():
        return MSG_EMPTY
    if len(dish.strip()) > MAX_DISH_LENGTH:
        return MSG_TOO_LONG
    if people not in PEOPLE_OPTIONS or fmt not in FORMAT_OPTIONS:
        return MSG_BAD_OPTION
    return None


# ----------------------------------------------------------------------------- промпт
SYSTEM_PROMPT = (
    "Ты — помощник по кулинарии и покупкам. Отвечай только на русском языке. "
    "Пиши только то, что просят: без вступлений, советов и заключений. "
    "Если запрос не похож на блюдо или кулинарную задачу, ответь одной строкой: "
    "«Не похоже на блюдо — уточните, что хотите приготовить.»"
)


def build_messages(dish: str, people: str, fmt: str) -> List[Dict[str, str]]:
    """Количество людей и формат задаются в запросе явно — модель не должна их угадывать."""
    lines = [
        f"Блюдо: {dish.strip()}",
        f"Количество порций: на {people} чел.",
        f"Составь список покупок, количества рассчитай на {people} чел.",
        "Формат списка: каждый продукт с новой строки, вида «- продукт — количество».",
    ]
    if fmt == FORMAT_STEPS:
        lines.append("После списка добавь строку «Шаги приготовления:» и 5–8 коротких "
                     "пронумерованных шагов, по одному предложению.")
    else:
        lines.append("Выведи только список продуктов, без шагов приготовления.")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


# ----------------------------------------------------------------------------- сертификат
@lru_cache(maxsize=1)
def _verify_setting():
    """Сертификаты GigaChat выпущены НУЦ Минцифры, которого нет в стандартном наборе Python.

    Если указан GIGACHAT_CA_BUNDLE, склеиваем стандартный набор certifi + этот сертификат
    (принимаем и PEM, и DER — в каком виде ни скачали бы файл). Проверку TLS не отключаем.
    """
    ca_path = os.getenv("GIGACHAT_CA_BUNDLE", "").strip()
    if not ca_path:
        return True
    path = Path(ca_path)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    if not path.exists():
        _log(logging.ERROR, "ca_bundle_not_found", path=str(path))
        raise AccessError("CA bundle not found")
    raw = path.read_bytes()
    pem = raw.decode("ascii", "ignore") if b"-----BEGIN" in raw else ssl.DER_cert_to_PEM_cert(raw)
    bundle = Path(tempfile.gettempdir()) / "gigachat_ca_bundle.pem"
    bundle.write_text(Path(certifi.where()).read_text(encoding="utf-8") + "\n" + pem, encoding="utf-8")
    return str(bundle)


# ----------------------------------------------------------------------------- HTTP
def _post(url: str, **kwargs) -> requests.Response:
    """Один HTTP-запрос с таймаутом; сетевые сбои -> классы ошибок."""
    try:
        return requests.post(url, timeout=(CONNECT_TIMEOUT, TIMEOUT), verify=_verify_setting(), **kwargs)
    except requests.exceptions.SSLError as exc:   # подкласс ConnectionError — ловим первым
        _log(logging.ERROR, "ssl_error", hint="проверьте GIGACHAT_CA_BUNDLE", error=type(exc).__name__)
        raise AccessError("ssl") from exc
    except requests.Timeout as exc:
        raise NetworkError("timeout") from exc
    except requests.ConnectionError as exc:
        raise NetworkError("connection") from exc


def _raise_for_status(response: requests.Response, stage: str) -> None:
    status = response.status_code
    if status < 400:
        return
    _log(logging.WARNING, "http_error", stage=stage, status=status)
    if status == 429:
        raise RateLimitError(str(status))
    if status >= 500:
        raise ServerError(str(status))
    raise AccessError(str(status))  # 400/401/403/404/413/422 — настройка или доступ


# ----------------------------------------------------------------------------- токен
_token_lock = threading.Lock()
_token: Dict[str, float] = {"value": "", "expires_at": 0.0}


def _get_token(force: bool = False) -> str:
    """Токен живёт 30 минут: кешируем и обновляем за минуту до истечения."""
    credentials = os.getenv("GIGACHAT_CREDENTIALS", "").strip()
    if not credentials:
        _log(logging.ERROR, "credentials_missing")
        raise AccessError("no credentials")

    with _token_lock:
        if not force and _token["value"] and time.time() < _token["expires_at"] - 60:
            return str(_token["value"])

        response = _post(
            AUTH_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={"scope": SCOPE},
        )
        _raise_for_status(response, "oauth")
        try:
            payload = response.json()
            token = payload["access_token"]
            expires_at = float(payload.get("expires_at", 0))
        except (ValueError, KeyError, TypeError) as exc:
            raise AccessError("bad oauth response") from exc

        if expires_at > 1e12:          # API отдаёт время в миллисекундах
            expires_at /= 1000
        _token["value"] = token
        _token["expires_at"] = expires_at or time.time() + 25 * 60
        _log(logging.INFO, "token_refreshed")
        return token


def reset_token_cache() -> None:
    _token["value"], _token["expires_at"] = "", 0.0


# ----------------------------------------------------------------------------- вызов модели
def _chat(messages: List[Dict[str, str]]) -> str:
    def call(token: str) -> requests.Response:
        return _post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            json={"model": MODEL, "messages": messages, "temperature": 0.3, "max_tokens": 800},
        )

    response = call(_get_token())
    if response.status_code == 401:    # токен мог истечь между проверкой и запросом — обновляем один раз
        _log(logging.INFO, "token_expired_retry")
        response = call(_get_token(force=True))
    _raise_for_status(response, "chat")

    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise EmptyAnswerError("bad format") from exc
    if not isinstance(content, str) or not content.strip():
        raise EmptyAnswerError("empty")
    return content.strip()


def _chat_with_retry(messages: List[Dict[str, str]]) -> str:
    """Максимум 1 повтор и только для временных ошибок: 5xx, таймаут, сеть."""
    try:
        return _chat(messages)
    except (ServerError, NetworkError) as exc:
        _log(logging.WARNING, "retry", error=type(exc).__name__, pause_s=RETRY_PAUSE_SECONDS)
        time.sleep(RETRY_PAUSE_SECONDS)
        return _chat(messages)


# ----------------------------------------------------------------------------- публичный API модуля
def generate(dish: str, people: str, fmt: str) -> Result:
    """Главная функция для UI: всегда возвращает Result и никогда не бросает исключения."""
    error = validate(dish, people, fmt)
    if error:
        _log(logging.INFO, "validation_failed", reason=error, dish_chars=len(dish or ""))
        return Result(False, error, "validation")

    started = time.perf_counter()
    try:
        answer = _chat_with_retry(build_messages(dish, people, fmt))
        elapsed = time.perf_counter() - started
        _log(logging.INFO, "llm_ok", model=MODEL, people=people, format=fmt,
             dish_chars=len(dish.strip()), answer_chars=len(answer), duration_s=round(elapsed, 2))
        return Result(True, answer, "ok", elapsed)
    except AccessError:
        message = MSG_ACCESS
    except RateLimitError:
        message = MSG_RATE_LIMIT
    except ServerError:
        message = MSG_SERVER
    except NetworkError:
        message = MSG_NETWORK
    except EmptyAnswerError:
        message = MSG_EMPTY_ANSWER
    except Exception:  # последний рубеж: подробности в лог, пользователю — одна фраза
        log.exception(json.dumps({"event": "unexpected_error"}))
        message = MSG_UNKNOWN

    elapsed = time.perf_counter() - started
    _log(logging.WARNING, "llm_failed", user_message=message, duration_s=round(elapsed, 2))
    return Result(False, message, "api_error", elapsed)


def generate_shopping_list(dish: str, people: str, format: str) -> str:  # noqa: A002 — имя из задания
    """Сигнатура из задания: вход dish, people, format — выход строка результата."""
    return generate(dish, people, format).text
