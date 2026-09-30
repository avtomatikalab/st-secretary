#!/bin/sh
# СТ-Секретарь — установка и обновление на macOS и Linux одной командой, без прав администратора:
#
#   curl -fsSL https://raw.githubusercontent.com/avtomatikalab/st-secretary/main/tools/install.sh | sh
#
# Скачивает с GitHub переносную версию для этой системы (последний выпуск), сверяет контрольную сумму, ставит в
# папку «СТ-Секретарь» в домашней папке и делает ярлык: на macOS — на Рабочем столе, в Linux — в меню программ.
# Повторный запуск обновляет программу: папки «данные» и «Резервные копии» не трогаются, прежняя версия
# остаётся в program.old. Файлы, скачанные так, macOS не помечает «карантином» — окна «не удалось проверить
# разработчика» не будет. В конце программа запускается.
#
# Для проверки: ST_INSTALL_ARCHIVE — готовый архив вместо скачивания, ST_INSTALL_DIR — куда ставить,
# ST_INSTALL_NO_START=1 — не запускать.
set -eu

REPO="avtomatikalab/st-secretary"
NAME="СТ-Секретарь"
DEST="${ST_INSTALL_DIR:-$HOME/$NAME}"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) SUFFIX="macos-arm64.tar.gz"; LAUNCHER="$NAME.command" ;;
  Darwin-x86_64) SUFFIX="macos-x86_64.tar.gz"; LAUNCHER="$NAME.command" ;;
  Linux-x86_64 | Linux-amd64) SUFFIX="linux-x86_64.tar.gz"; LAUNCHER="$NAME.sh" ;;
  *)
    echo "Для $(uname -s) $(uname -m) готовой сборки нет. Как запустить из исходного кода:"
    echo "https://github.com/$REPO"
    exit 1 ;;
esac

if [ -d "$DEST/program" ] && curl -fs --max-time 2 http://127.0.0.1:8765/health >/dev/null 2>&1; then
  echo "СТ-Секретарь сейчас запущен. Выключите его (кнопка «Выключить» вверху страницы) и запустите команду снова."
  exit 1
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

if [ -n "${ST_INSTALL_ARCHIVE:-}" ]; then
  cp "$ST_INSTALL_ARCHIVE" "$TMP/app.tar.gz"
else
  echo "Узнаю последнюю версию на GitHub..."
  curl -fsSL -H "Accept: application/vnd.github+json" "https://api.github.com/repos/$REPO/releases/latest" \
    | tr ',{}' '\n\n\n' > "$TMP/release.txt"
  URL=$(grep -o "\"browser_download_url\": *\"https://github.com/$REPO/releases/download/[^\"]*-$SUFFIX\"" \
    "$TMP/release.txt" | head -1 | sed 's/.*"\(https[^"]*\)"$/\1/')
  if [ -z "$URL" ]; then
    echo "В последнем выпуске нет сборки для этой системы ($SUFFIX)."
    exit 1
  fi
  # контрольная сумма архива, которую публикует GitHub: поле digest того же файла (оно идёт раньше ссылки)
  SHA=$(awk -v s="-$SUFFIX\"" '/"digest":/ { d = $0 } /"browser_download_url":/ && index($0, s) { print d; exit }' \
    "$TMP/release.txt" | sed -n 's/.*sha256:\([0-9a-f]\{64\}\).*/\1/p')
  echo "Скачиваю $URL"
  curl -fL --progress-bar "$URL" -o "$TMP/app.tar.gz"
  if [ -n "$SHA" ]; then
    if command -v sha256sum >/dev/null 2>&1; then GOT=$(sha256sum "$TMP/app.tar.gz" | cut -d' ' -f1)
    else GOT=$(shasum -a 256 "$TMP/app.tar.gz" | cut -d' ' -f1); fi
    if [ "$GOT" != "$SHA" ]; then
      echo "Архив скачался с ошибкой (не совпала контрольная сумма). Запустите команду ещё раз."
      exit 1
    fi
  else
    echo "GitHub не сообщил контрольную сумму — проверка пропущена (скачано по защищённому соединению)."
  fi
fi

mkdir "$TMP/x"
tar -xzf "$TMP/app.tar.gz" -C "$TMP/x"
NEW="$TMP/x/$NAME"
if [ ! -x "$NEW/program/python/bin/python3" ]; then
  echo "В архиве нет программы — это не архив СТ-Секретаря."
  exit 1
fi

mkdir -p "$DEST"
if [ -d "$DEST/program" ]; then
  echo "Обновляю $DEST — соревнования и резервные копии остаются на месте."
  rm -rf "$DEST/program.old" "$DEST/program.new" "$DEST/program.next"
  cp -R "$NEW/program" "$DEST/program.next"   # сначала рядом, потом быстрая замена
  mv "$DEST/program" "$DEST/program.old"
  mv "$DEST/program.next" "$DEST/program"
else
  echo "Ставлю в $DEST"
  cp -R "$NEW/program" "$DEST/program"
fi
for f in "$NEW"/*; do  # файл запуска, «Прочтите меня», инструкция: новая копия рядом и переименование
  if [ -f "$f" ]; then
    base=$(basename "$f")
    cp -p "$f" "$DEST/.$base.new"
    mv -f "$DEST/.$base.new" "$DEST/$base"
  fi
done
chmod +x "$DEST/$LAUNCHER"

if [ "$(uname -s)" = "Darwin" ]; then
  DESK="$HOME/Desktop"
  mkdir -p "$DESK"
  printf '#!/bin/bash\nexec "%s" "$@"\n' "$DEST/$LAUNCHER" > "$DESK/$NAME.command"
  chmod +x "$DESK/$NAME.command"
  WHERE="ярлык «$NAME» на Рабочем столе (двойной щелчок)"
else
  APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  mkdir -p "$APPS"
  ICON=$(ls "$DEST"/program/python/lib/python3*/site-packages/st_secretary/web/static/icon.svg 2>/dev/null | head -1)
  cat > "$APPS/st-secretary.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$NAME
Comment=Секретариат соревнований по спортивному туризму
Exec="$DEST/$LAUNCHER"
Icon=$ICON
Terminal=true
Categories=Office;
EOF
  WHERE="«$NAME» в меню программ или файл $DEST/$LAUNCHER"
fi

echo
echo "Готово. Запуск: $WHERE."
echo "Соревнования будут в папке $DEST/данные."
if [ -z "${ST_INSTALL_NO_START:-}" ]; then
  echo "Запускаю..."
  rm -rf "$TMP"
  trap - EXIT
  exec "$DEST/$LAUNCHER" </dev/tty
fi
