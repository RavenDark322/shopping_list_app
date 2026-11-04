"""Проверка доступа к GigaChat без интерфейса: получает токен и выводит доступные модели.

Запуск:  python scripts/check_access.py
Токен и ключ на экран не выводятся.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import llm  # noqa: E402


def main() -> None:
    print(f"Адрес токенов: {llm.AUTH_URL}")
    print(f"Адрес API:     {llm.BASE_URL}")
    print(f"Scope:         {llm.SCOPE}")
    print(f"Модель:        {llm.MODEL}")
    print(f"Ключ задан:    {'да' if llm.os.getenv('GIGACHAT_CREDENTIALS') else 'НЕТ — заполните GIGACHAT_CREDENTIALS в .env'}")
    print(f"Сертификат:    {llm.os.getenv('GIGACHAT_CA_BUNDLE') or 'не указан (GIGACHAT_CA_BUNDLE)'}")

    try:
        token = llm._get_token(force=True)
        print("\n✅ Токен получен.")
    except llm.AccessError as exc:
        print(f"\n❌ Не удалось получить токен ({exc}). Подробности — logs/app.log.")
        return
    except llm.NetworkError as exc:
        print(f"\n❌ Сеть недоступна ({exc}).")
        return

    try:
        resp = requests.get(f"{llm.BASE_URL}/models", headers={"Authorization": f"Bearer {token}"},
                            timeout=15, verify=llm._verify_setting())
        models = [m.get("id") for m in resp.json().get("data", [])]
        print(f"✅ Доступные модели: {', '.join(models) or '(список пуст)'}")
        if llm.MODEL not in models and models:
            print(f"⚠️  Модели {llm.MODEL} нет в списке — укажите одну из доступных в GIGACHAT_MODEL.")
    except Exception as exc:  # диагностический скрипт: показываем только тип ошибки
        print(f"⚠️  Список моделей получить не удалось: {type(exc).__name__}")


if __name__ == "__main__":
    main()
