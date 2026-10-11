# Приём сообщений «Сообщить» на сервере (решение 050)

Программа сама отправляет собранный zip на `FEEDBACK_URL` (`src/st_secretary/feedback.py`), разработчик забирает
его ключом. Пароли от почты и сервера в код программы не попадают.

| Файл | Что это |
|---|---|
| `receiver.py` | приёмник: только стандартная библиотека Python 3.12+, слушает `127.0.0.1:8090` |
| `st-feedback.service` | служба systemd: отдельный пользователь без прав, только своя папка, до 96 МБ памяти |
| `nginx.conf` | кусок настроек nginx: HTTPS снаружи, ограничение частоты и размера |

## Установка (Ubuntu, nginx и сертификат на имя уже есть)
```
mkdir -p /opt/st-feedback /etc/st-feedback
cp receiver.py /opt/st-feedback/
openssl rand -hex 24 > /etc/st-feedback/token && chmod 600 /etc/st-feedback/token
cp st-feedback.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now st-feedback
```
Затем строки из `nginx.conf` — в `server { … }` сайта и `limit_req_zone` — в `http { … }`; `nginx -t && systemctl reload nginx`.
Проверка: `curl https://<имя>/api/health` → `{"ok": true}`.

Ключ для забора (`/etc/st-feedback/token`) — только у разработчика, в репозиторий не класть.

## Забор
- `GET /api/pickup` с заголовком `Authorization: Bearer <ключ>` — список `[{"id", "size"}]`;
- `GET /api/pickup/<id>` — zip; `DELETE /api/pickup/<id>` — удалить с сервера, когда забрали.

Утренний разбор на домашнем ноутбуке забирает их сам (скрипт `fetch_mail.py` в «СТ секритариат/Разбор сообщений»).

## Ограничения
До 25 МБ за раз, распакованный объём до 80 МБ, только `.md/.png/.txt/.json` без путей и с `manifest.json`
программы; в папке ожидающих — до 500 МБ, до 300 приёмов в сутки; nginx — не чаще 20 приёмов в минуту на всех.
Сервер ничего из присланного не выполняет и не хранит IP отправителей.
