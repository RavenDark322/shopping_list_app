Сюда положите корневой сертификат НУЦ Минцифры «Russian Trusted Root CA»
(скачивается на https://www.gosuslugi.ru/crt), например под именем `russian_trusted_root_ca.cer`,
и укажите путь в `.env`: `GIGACHAT_CA_BUNDLE=certs/russian_trusted_root_ca.cer`.

Подходит файл и в формате PEM, и в формате DER — приложение распознаёт оба.
Сам сертификат публичный, но в репозиторий не коммитится (см. .gitignore).
