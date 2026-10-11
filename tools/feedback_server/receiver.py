"""Приём сообщений «Сообщить» на сервере (решение 050): программа отправляет zip сама, без почты.

Только стандартная библиотека Python — ставится на любой сервер одной копией файла. Слушает 127.0.0.1 (снаружи —
nginx с HTTPS). Никаких паролей в коде программы: принимать может любой, забирать — только с ключом.

    POST /api/feedback            — zip «СТ-Секретарь — сообщения …» (как bundle() в feedback.py) → {"ok": true, "id": …}
    GET  /api/pickup              — список ожидающих (заголовок Authorization: Bearer <ключ>)
    GET  /api/pickup/<id>         — сам zip
    DELETE /api/pickup/<id>       — забрали — удалить с сервера

Проверки: размер, это zip программы (manifest.json с "app": "st-secretary"), внутри только файлы без путей с
расширениями .md/.png/.txt/.json, распакованный объём ограничен (защита от «zip-бомбы»), общий объём папки и число
приёмов в сутки ограничены — переполнить диск сервера нельзя. Содержимое — данные, сервер ничего из них не выполняет.

    python3 receiver.py --dir /var/lib/st-feedback --token-file /etc/st-feedback/token [--port 8090]
"""

from __future__ import annotations

import argparse
import hmac
import io
import json
import re
import secrets
import zipfile
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_UPLOAD = 25 * 1024 * 1024  # как вложение Gmail: что не пролезет сюда, не пролезло бы и в письмо
MAX_UNPACKED = 80 * 1024 * 1024
MAX_FILES = 300
MAX_STORED = 500 * 1024 * 1024  # вся папка ожидающих
MAX_PER_DAY = 300
ALLOWED = (".md", ".png", ".txt", ".json")
ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


class Store:
    def __init__(self, root: Path, token: str):
        self.root = root
        self.token = token
        root.mkdir(parents=True, exist_ok=True)

    def files(self) -> list[Path]:
        return sorted(p for p in self.root.glob("*.zip") if ID_RE.match(p.stem))

    def check(self, data: bytes) -> str | None:
        """Ошибка по-русски или None, если это годный zip программы."""
        if len(data) > MAX_UPLOAD:
            return "файл больше 25 МБ"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                infos = z.infolist()
                if not infos or len(infos) > MAX_FILES:
                    return "в архиве нет файлов или их слишком много"
                if sum(i.file_size for i in infos) > MAX_UNPACKED:
                    return "архив слишком большой в распакованном виде"
                for i in infos:
                    name = i.filename
                    if name != Path(name).name or "\\" in name or name.startswith(".") or not name.endswith(ALLOWED):
                        return "в архиве лишние файлы"
                manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError, OSError):
            return "это не файл сообщений СТ-Секретаря"
        if not isinstance(manifest, dict) or manifest.get("app") != "st-secretary":
            return "это не файл сообщений СТ-Секретаря"
        return None

    def save(self, data: bytes) -> tuple[str | None, str | None]:
        """(id, None) — принято; (None, ошибка) — нет."""
        error = self.check(data)
        if error:
            return None, error
        files = self.files()
        if sum(p.stat().st_size for p in files) + len(data) > MAX_STORED:
            return None, "сервер переполнен, отправьте письмом"
        day = datetime.now(UTC).strftime("%Y%m%d")
        if sum(1 for p in files if p.stem.startswith(day)) >= MAX_PER_DAY:
            return None, "сегодня принято слишком много, отправьте письмом"
        ident = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
        tmp = self.root / f".{ident}.part"
        tmp.write_bytes(data)
        tmp.replace(self.root / f"{ident}.zip")
        return ident, None

    def authorized(self, header: str | None) -> bool:
        given = (header or "").removeprefix("Bearer ").strip()
        return bool(self.token) and hmac.compare_digest(given.encode(), self.token.encode())

    def path(self, ident: str) -> Path | None:
        p = self.root / f"{ident}.zip"
        return p if ID_RE.match(ident) and p.is_file() else None


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "st-feedback"
        sys_version = ""

        def log_message(self, fmt, *args):  # без IP и тел запросов: журнал systemd — только метод, путь, код
            pass

        def reply(self, code: int, obj: dict | list | None = None, body: bytes | None = None,
                  ctype: str = "application/json; charset=utf-8"):
            data = body if body is not None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            print(f"{self.command} {self.path.split('?')[0]} {code}", flush=True)

        def do_POST(self):  # имя метода задаёт http.server
            if self.path.split("?")[0] != "/api/feedback":
                return self.reply(404, {"ok": False, "error": "нет такого адреса"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_UPLOAD:
                return self.reply(413, {"ok": False, "error": "файл пустой или больше 25 МБ"})
            ident, error = store.save(self.rfile.read(length))
            if error:
                return self.reply(422 if "не файл" in error or "лишние" in error else 507, {"ok": False, "error": error})
            return self.reply(200, {"ok": True, "id": ident})

        def pickup(self) -> str | None:
            """id из /api/pickup/<id>, «» для списка; None — не тот адрес или нет ключа (ответ уже отправлен)."""
            path = self.path.split("?")[0].rstrip("/")
            if path != "/api/pickup" and not path.startswith("/api/pickup/"):
                self.reply(404, {"ok": False, "error": "нет такого адреса"})
                return None
            if not store.authorized(self.headers.get("Authorization")):
                self.reply(403, {"ok": False, "error": "нужен ключ"})
                return None
            return path.removeprefix("/api/pickup").lstrip("/")

        def do_GET(self):
            if self.path.split("?")[0] == "/api/health":
                return self.reply(200, {"ok": True})
            ident = self.pickup()
            if ident is None:
                return None
            if not ident:
                return self.reply(200, [{"id": p.stem, "size": p.stat().st_size} for p in store.files()])
            p = store.path(ident)
            if p is None:
                return self.reply(404, {"ok": False, "error": "нет такого"})
            return self.reply(200, body=p.read_bytes(), ctype="application/zip")

        def do_DELETE(self):
            ident = self.pickup()
            if ident is None:
                return None
            p = store.path(ident)
            if p is None:
                return self.reply(404, {"ok": False, "error": "нет такого"})
            p.unlink()
            return self.reply(200, {"ok": True})

    return Handler


def serve(root: Path, token: str, host: str = "127.0.0.1", port: int = 8090) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(Store(root, token)))
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--token-file", type=Path, required=True)
    ap.add_argument("--port", type=int, default=8090)
    args = ap.parse_args(argv)
    token = args.token_file.read_text(encoding="utf-8").strip()
    if len(token) < 20:
        raise SystemExit("ключ для забора слишком короткий (нужно 20+ символов)")
    server = serve(args.dir, token, port=args.port)
    print(f"st-feedback: 127.0.0.1:{args.port}, папка {args.dir}", flush=True)
    try:
        server.serve_forever(poll_interval=1)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
