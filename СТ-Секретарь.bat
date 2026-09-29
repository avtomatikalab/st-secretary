@echo off
rem ST-Secretary launcher: double-click to open the program in the browser.
rem Competitions are stored in the "данные" folder next to this file.
rem This file is saved in CP866 (the Russian Windows console code page) so that messages display correctly.
rem The window never closes by itself: it is the program; the user stops it with the "Выключить" button or closes it.
cd /d "%~dp0"
title СТ-Секретарь

rem Libraries live in the local profile, not next to the program: the program folder may be inside a cloud
rem folder (Google Drive), which locks files while syncing and breaks the update of libraries.
set "UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\st-secretary\venv"

set "UV=uv"
where uv >nul 2>nul
if errorlevel 1 (
  if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "UV=%USERPROFILE%\.local\bin\uv.exe"
  ) else (
    echo.
    echo Не найдена программа uv, через которую запускается СТ-Секретарь.
    echo Установите её один раз по инструкции: https://docs.astral.sh/uv/getting-started/installation/
    echo и снова запустите этот файл.
    echo.
    pause
    exit /b 1
  )
)

echo Запускаю СТ-Секретарь... Первый запуск может занять пару минут.
echo.
"%UV%" run st-secretary web
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" (
  echo Программа остановилась с ошибкой. Сообщение выше пригодится разработчикам.
)
echo Это окно можно закрыть.
pause
