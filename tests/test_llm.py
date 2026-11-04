"""Автотесты обработчика: python -m pytest -q   (настоящий GigaChat не вызывается)."""
import pytest

import llm


# ---------- три сценария из задания ----------
def test_normal_input_returns_shopping_list(fake):
    result = llm.generate("Борщ с говядиной", "4", llm.FORMAT_LIST)
    assert result.ok and result.kind == "ok"
    assert "- " in result.text and "600 г" in result.text      # 150 г × 4 человека — люди переданы явно
    assert "Шаги" not in result.text


def test_steps_format_adds_steps(fake):
    result = llm.generate("Омлет", "1", llm.FORMAT_STEPS)
    assert result.ok and "Шаги приготовления:" in result.text


@pytest.mark.parametrize("dish", ["", "   ", "\n\t"])
def test_empty_input_does_not_call_api(fake, dish):
    result = llm.generate(dish, "2", llm.FORMAT_LIST)
    assert (result.ok, result.kind, result.text) == (False, "validation", llm.MSG_EMPTY)
    assert fake["calls"] == 0


def test_too_long_input_does_not_call_api(fake):
    result = llm.generate("я" * (llm.MAX_DISH_LENGTH + 1), "2", llm.FORMAT_LIST)
    assert (result.kind, result.text) == ("validation", llm.MSG_TOO_LONG)
    assert fake["calls"] == 0


def test_max_length_input_is_accepted(fake):
    assert llm.generate("я" * llm.MAX_DISH_LENGTH, "2", llm.FORMAT_LIST).ok


def test_unknown_option_rejected(fake):
    assert llm.generate("Плов", "100", llm.FORMAT_LIST).text == llm.MSG_BAD_OPTION
    assert fake["calls"] == 0


# ---------- промпт ----------
def test_prompt_controls_people_and_format_explicitly():
    user = llm.build_messages("Плов", "6", llm.FORMAT_STEPS)[1]["content"]
    assert "на 6 чел" in user and "5–8" in user
    user = llm.build_messages("Плов", "1", llm.FORMAT_LIST)[1]["content"]
    assert "без шагов" in user


# ---------- карта ошибок API ----------
def test_missing_key_gives_access_message_without_api_call(fake, monkeypatch):
    monkeypatch.delenv("GIGACHAT_CREDENTIALS")
    result = llm.generate("Плов", "2", llm.FORMAT_LIST)
    assert (result.kind, result.text) == ("api_error", llm.MSG_ACCESS)
    assert fake["calls"] == 0


def test_wrong_key_401_no_retry(fake):
    fake["mode"] = "unauthorized"
    result = llm.generate("Плов", "2", llm.FORMAT_LIST)
    assert result.text == llm.MSG_ACCESS
    assert fake["calls"] == 1                                   # 4xx не повторяем


def test_server_error_retried_once(fake):
    fake["mode"] = "error500"
    result = llm.generate("Плов", "2", llm.FORMAT_LIST)
    assert result.text == llm.MSG_SERVER
    assert fake["calls"] == 3                                   # токен + 2 попытки к модели


def test_timeout_retried_once(fake):
    fake["mode"] = "slow"
    result = llm.generate("Плов", "2", llm.FORMAT_LIST)
    assert result.text == llm.MSG_NETWORK
    assert 2 <= result.elapsed < 5                              # 2 попытки по 1 с, а не 60 с


def test_network_down(fake, monkeypatch):
    monkeypatch.setattr(llm, "BASE_URL", "http://127.0.0.1:9/v1")
    assert llm.generate("Плов", "2", llm.FORMAT_LIST).text == llm.MSG_NETWORK


@pytest.mark.parametrize("mode", ["empty", "badjson"])
def test_empty_or_broken_answer(fake, mode):
    fake["mode"] = mode
    assert llm.generate("Плов", "2", llm.FORMAT_LIST).text == llm.MSG_EMPTY_ANSWER


def test_token_is_cached(fake):
    llm.generate("Плов", "2", llm.FORMAT_LIST)
    llm.generate("Борщ", "2", llm.FORMAT_LIST)
    assert fake["calls"] == 3                                   # 1 токен + 2 запроса к модели


def test_unexpected_error_is_not_raised(fake, monkeypatch):
    monkeypatch.setattr(llm, "_chat_with_retry", lambda m: 1 / 0)
    result = llm.generate("Плов", "2", llm.FORMAT_LIST)
    assert (result.ok, result.text) == (False, llm.MSG_UNKNOWN)


# ---------- безопасность ----------
def test_no_secrets_or_user_text_in_log(fake):
    llm.generate("Секретный пирог бабушки", "2", llm.FORMAT_LIST)
    fake["mode"] = "unauthorized"
    llm.reset_token_cache()
    llm.generate("Плов", "2", llm.FORMAT_LIST)
    log_text = (llm.LOG_DIR / "app.log").read_text(encoding="utf-8")
    assert "fake-key" not in log_text and "fake-token" not in log_text
    assert "Секретный пирог" not in log_text                     # пишем длины, а не текст


def test_signature_from_assignment_returns_string(fake):
    assert isinstance(llm.generate_shopping_list("Плов", "2", llm.FORMAT_LIST), str)


# ---------- сертификат НУЦ Минцифры: PEM и DER ----------
@pytest.mark.parametrize("as_der", [False, True])
def test_ca_bundle_accepts_pem_and_der(tmp_path, monkeypatch, as_der):
    import ssl, certifi
    body = certifi.contents().split("-----END CERTIFICATE-----")[0]
    pem = "-----BEGIN" + body.split("-----BEGIN", 1)[1] + "-----END CERTIFICATE-----\n"
    cert = tmp_path / "russian_trusted_root_ca.cer"
    cert.write_bytes(ssl.PEM_cert_to_DER_cert(pem) if as_der else pem.encode())
    monkeypatch.setenv("GIGACHAT_CA_BUNDLE", str(cert))
    llm._verify_setting.cache_clear()
    try:
        bundle = llm._verify_setting()
        text = open(bundle, encoding="utf-8").read()
        assert text.count("BEGIN CERTIFICATE") > 100      # certifi + наш сертификат
    finally:
        llm._verify_setting.cache_clear()


def test_missing_ca_file_gives_access_message(fake, monkeypatch):
    monkeypatch.setenv("GIGACHAT_CA_BUNDLE", "certs/nope.cer")
    llm._verify_setting.cache_clear()
    try:
        assert llm.generate("Плов", "2", llm.FORMAT_LIST).text == llm.MSG_ACCESS
    finally:
        llm._verify_setting.cache_clear()
