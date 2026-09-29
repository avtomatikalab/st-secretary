"""Местное табло: результаты на телефонах участников и зрителей через Wi-Fi ноутбука секретариата.

Основная программа открывается только на этом компьютере (127.0.0.1): в ней заявки, паспорта, договоры.
Табло — отдельный маленький сервер только для чтения: он показывает результаты соревнований, для которых
секретарь включил табло, и больше ничего. Страница обновляется сама раз в 30 секунд; без скриптов
и без интернета — открывается на любом телефоне.

Запускается кнопкой на странице «Онлайн-табло» и слушает все сетевые адреса ноутбука (0.0.0.0) — при первом
включении Windows спросит разрешение в брандмауэре, нужно разрешить для частной сети.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

HERE = Path(__file__).parent
BOARD_PORT = 8780
REFRESH_SECONDS = 30


def lan_addresses() -> list[str]:
    """Адреса ноутбука в локальной сети (Wi-Fi, точка доступа) — по ним телефоны открывают табло."""
    ips: set[str] = set()
    try:  # адрес «наружу» — без отправки данных; без сети может не сработать
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.", "0.")))


def qr_svg(text: str) -> str:
    """QR-код адреса — навести камеру телефона. Без библиотеки segno — пусто (адрес виден текстом)."""
    try:
        import segno
    except ImportError:
        return ""
    return segno.make(text, error="m").svg_inline(scale=6, dark="#1a2027", light="#ffffff")


def create_board_app(boards: Callable[[], list[dict]], board: Callable[[str], dict | None]) -> FastAPI:
    """boards() — соревнования с включённым табло: [{"id", "title", "dates"}];
    board(id) — {"title", "dates", "place", "zachety": [{"z", "run", "state", "label"}], …} или None."""
    app = FastAPI(title="СТ-Секретарь — табло", docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=HERE / "templates")

    def render(request: Request, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, "board.html", {"refresh": REFRESH_SECONDS, "prefix": "", **ctx})

    @app.get("/")
    def index(request: Request):
        items = boards()
        if len(items) == 1:
            return render(request, data=board(items[0]["id"]), items=items)
        return render(request, data=None, items=items)

    @app.get("/c/{cid}")
    def competition(request: Request, cid: str):
        data = board(cid)
        if data is None:
            raise HTTPException(404)
        return render(request, data=data, items=boards())

    @app.get("/health")
    def health():
        return {"app": "st-secretary-board"}

    return app


class BoardServer:
    """Сервер табло в отдельном потоке: включить, выключить, узнать адреса."""

    def __init__(self, app: FastAPI, port: int = BOARD_PORT, host: str = "0.0.0.0"):  # табло — для сети
        self.app, self.port, self.host = app, port, host
        self.server = None
        self.error = ""

    @property
    def running(self) -> bool:
        return bool(self.server and self.server.started and not self.server.should_exit)

    def _free_port(self) -> int:
        for port in range(self.port, self.port + 20):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind((self.host, port))
                    return port
                except OSError:
                    continue
        raise OSError(f"порты {self.port}–{self.port + 19} заняты")

    def start(self) -> bool:
        if self.running:
            return True
        import uvicorn

        self.error = ""
        try:
            port = self._free_port()
        except OSError as e:
            self.error = str(e)
            return False
        self.server = uvicorn.Server(uvicorn.Config(self.app, host=self.host, port=port, log_level="warning"))
        self.port = port
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(100):  # до 10 секунд
            if self.server.started:
                return True
            time.sleep(0.1)
        self.error = "табло не запустилось"
        return False

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True

    def urls(self) -> list[str]:
        return [f"http://{ip}:{self.port}/" for ip in lan_addresses()]
