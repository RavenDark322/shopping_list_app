"""Фейковый GigaChat для проверки обработки ошибок без настоящего ключа.

Запуск:  python scripts/fake_gigachat.py <режим>        (слушает http://127.0.0.1:8002)

Режимы:
  ok            — нормальный список покупок
  unauthorized  — 401 при получении токена (неверный ключ)
  error500      — 500 на запрос к модели (сервис упал)
  slow          — модель «думает» 60 секунд (таймаут)
  empty         — пустой ответ модели
  badjson       — ответ не в формате JSON

Чтобы приложение ходило сюда, в .env временно укажите:
  GIGACHAT_CREDENTIALS=fake
  GIGACHAT_AUTH_URL=http://127.0.0.1:8002/api/v2/oauth
  GIGACHAT_BASE_URL=http://127.0.0.1:8002/v1
"""
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST, PORT = "127.0.0.1", 8002
MODES = ("ok", "unauthorized", "error500", "slow", "empty", "badjson")


def make_handler(mode_ref):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802
            mode = mode_ref["mode"]
            mode_ref["calls"] = mode_ref.get("calls", 0) + 1
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)

            if self.path.endswith("/oauth"):
                if mode == "unauthorized" or not self.headers.get("Authorization", "").startswith("Basic "):
                    return self._send(401, {"code": 6, "message": "fake: credentials doesn't match db data"})
                return self._send(200, {"access_token": "fake-token",
                                        "expires_at": int((time.time() + 1800) * 1000)})

            if self.path.endswith("/chat/completions"):
                if mode == "error500":
                    return self._send(500, {"status": 500, "message": "fake: internal error"})
                if mode == "slow":
                    time.sleep(60)
                if mode == "badjson":
                    return self._send(200, b"<html>not json</html>")
                content = "" if mode == "empty" else fake_answer(json.loads(body or b"{}"))
                return self._send(200, {"choices": [{"index": 0, "finish_reason": "stop",
                                                     "message": {"role": "assistant", "content": content}}],
                                        "model": "fake-gigachat", "object": "chat.completion"})
            return self._send(404, {"message": "not found"})

        def log_message(self, fmt, *args):
            print(f"[fake-gigachat:{mode_ref['mode']}] {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    return Handler


def fake_answer(request):
    prompt = request.get("messages", [{}])[-1].get("content", "")
    dish = re.search(r"Блюдо: (.+)", prompt)
    people = re.search(r"на (\d+) чел", prompt)
    n = int(people.group(1)) if people else 2
    text = (f"- Основной продукт для «{dish.group(1) if dish else 'блюда'}» — {150 * n} г\n"
            f"- Лук репчатый — {n} шт.\n- Соль — по вкусу")
    if "Шаги приготовления" in prompt:
        text += "\n\nШаги приготовления:\n" + "\n".join(f"{i}. Фейковый шаг {i}." for i in range(1, 6))
    return text


def serve(mode="ok", port=PORT):
    mode_ref = {"mode": mode, "calls": 0}
    server = ThreadingHTTPServer((HOST, port), make_handler(mode_ref))
    server.daemon_threads = True
    return server, mode_ref


if __name__ == "__main__":
    selected = sys.argv[1] if len(sys.argv) > 1 else "ok"
    if selected not in MODES:
        sys.exit(f"Неизвестный режим. Доступны: {', '.join(MODES)}")
    srv, _ = serve(selected)
    print(f"Fake GigaChat [{selected}] on http://{HOST}:{PORT}  (Ctrl+C — остановить)")
    srv.serve_forever()
