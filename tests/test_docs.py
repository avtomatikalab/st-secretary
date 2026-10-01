"""Документация для программиста не расходится с кодом молча (Правки, п. 44): docs/developer-guide.md.

1. Каждый файл кода, шаблон, файл web/static и tools упомянут (путь или имя в `обратных кавычках`).
2. Упомянутые пути к файлам существуют.
3. `модуль.имя` и `Класс.имя` — в файле модуля (класса) есть def, class или присваивание с этим именем.
4. Ссылки оглавления ведут на существующие заголовки, ссылки на файлы — на существующие файлы.
Сообщения — что поправить в документации.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "st_secretary"
GUIDE = ROOT / "docs" / "developer-guide.md"
TEXT = GUIDE.read_text(encoding="utf-8")
TOKENS = set(re.findall(r"`([^`\n]+)`", TEXT))  # всё, что в обратных кавычках
NAMES = {Path(t).name for t in TOKENS}
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", ".pytest_cache", ".ruff_cache"}
FILE = re.compile(r"^[A-Za-z0-9_./-]+\.(py|html|js|css|toml|md|sh|bat|svg|json|txt)$")
WHERE = "docs/developer-guide.md"
OBJECTS = {"app", "cx", "request", "self"}  # объекты, а не модули: app.state, cx.store


def _mentioned(rel: str) -> bool:
    return rel in TOKENS or Path(rel).name in TOKENS or any(t.endswith("/" + rel) for t in TOKENS)


def _repo_files():
    for p in ROOT.rglob("*"):
        if p.is_file() and not SKIP_DIRS & set(p.relative_to(ROOT).parts):
            yield p


def test_every_code_file_template_static_and_tool_is_in_the_guide():
    missing = []
    for p in sorted(SRC.rglob("*.py")):
        if "__pycache__" in p.parts or (p.name == "__init__.py" and not p.read_text(encoding="utf-8").strip()):
            continue
        rel = p.relative_to(SRC).as_posix()
        if not _mentioned(rel):
            missing.append(f"нет {rel} — добавьте строку в «Карту файлов» (раздел 6)")
    for p in sorted((SRC / "web" / "templates").glob("*.html")):
        if p.name not in NAMES:
            missing.append(f"нет шаблона {p.name} — впишите его полным именем в таблицу страниц (раздел 6)")
    for p in sorted((SRC / "web" / "static").iterdir()):
        if p.is_file() and p.name not in NAMES:
            missing.append(f"нет web/static/{p.name} — добавьте в таблицу «Программа в браузере»")
    for p in sorted((ROOT / "tools").iterdir()):
        if p.is_file() and p.suffix in (".py", ".sh") and f"tools/{p.name}" not in TOKENS and p.name not in NAMES:
            missing.append(f"нет tools/{p.name} — добавьте в таблицу «Остальное»")
    assert not missing, f"{WHERE}:\n" + "\n".join(missing)


def test_mentioned_files_exist():
    names = {p.name for p in _repo_files()}
    bases = [ROOT, SRC, SRC / "web", SRC / "reference" / "data", ROOT / "docs"]
    gone = []
    for t in sorted(TOKENS):
        if not FILE.match(t) or "*" in t:
            continue
        ok = any((b / t).exists() for b in bases) if "/" in t else t in names  # имя без пути — где угодно в репо
        if not ok:
            gone.append(f"упомянут {t} — такого файла нет (переименован или удалён?)")
    assert not gone, f"{WHERE}:\n" + "\n".join(gone)


def _module_file(mod: str) -> Path | None:
    for p in (SRC / f"{mod}.py", SRC / "web" / f"{mod}.py", SRC / "web" / "pages" / f"{mod}.py",
              SRC / "importers" / f"{mod}.py", SRC / "exporters" / f"{mod}.py", SRC / "disciplines" / f"{mod}.py"):
        if p.is_file():
            return p
    return None


def _has(text: str, name: str) -> bool:
    """def, class, присваивание (и поле объекта: self.имя =) с этим именем."""
    n = re.escape(name)
    return bool(re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{n}\b|^\s*(?:self\.)?{n}\s*[:=]", text, re.MULTILINE))


def test_mentioned_functions_and_classes_exist():
    classes: dict[str, Path] = {}
    for p in SRC.rglob("*.py"):
        for name in re.findall(r"^class\s+(\w+)", p.read_text(encoding="utf-8"), re.MULTILINE):
            classes.setdefault(name, p)
    gone = []
    for t in sorted(TOKENS):
        m = re.fullmatch(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)(?:\(\))?", t)
        if not m or FILE.match(t):
            continue
        owner, name = m.groups()
        if owner in OBJECTS:
            continue
        path = _module_file(owner) if owner[0].islower() else classes.get(owner)
        if path is None:
            continue  # не модуль и не класс программы (например, cx.store, app.state)
        if not _has(path.read_text(encoding="utf-8"), name):
            gone.append(f"упомянута {t} — в {path.relative_to(SRC).as_posix()} её нет (переименована?)")
    assert not gone, f"{WHERE}:\n" + "\n".join(gone)


def _slug(heading: str) -> str:
    """Якорь заголовка, как его делает GitHub: строчные, без знаков, пробелы — дефисы."""
    s = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return s.replace(" ", "-")


def test_links_lead_somewhere():
    anchors = {_slug(h) for h in re.findall(r"^#{1,6}\s+(.+)$", TEXT, re.MULTILINE)}
    bad = []
    for target in re.findall(r"\]\(([^)\s]+)\)", TEXT):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if target.startswith("#"):
            if target[1:] not in anchors:
                bad.append(f"ссылка {target} — нет такого заголовка (заголовок переименован?)")
        elif not (GUIDE.parent / target.split("#")[0]).exists():
            bad.append(f"ссылка на {target} — такого файла нет")
    assert not bad, f"{WHERE}:\n" + "\n".join(bad)
