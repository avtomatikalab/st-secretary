@echo off
rem ST-Secretary launcher FROM SOURCE (for developers, needs uv). Secretaries and judges use the ready
rem program instead: https://github.com/avtomatikalab/st-secretary/releases/latest
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
    echo Это исходный код СТ-Секретаря - он для разработчиков, ему нужна программа uv.
    echo.
    echo Секретарям и судьям нужна готовая программа: в ней уже всё есть, ничего устанавливать
    echo не нужно - ни Python, ни PowerShell, ни uv. Открываю страницу, где её скачать:
    echo https://github.com/avtomatikalab/st-secretary/releases/latest
    echo Скачайте st-secretary-windows.zip, распакуйте и запустите СТ-Секретарь.bat из распакованной папки.
    echo.
    start "" "https://github.com/avtomatikalab/st-secretary/releases/latest"
    pause
    exit /b 1
  )
)

:start
echo Запускаю СТ-Секретарь... Первый запуск может занять пару минут.
echo.
"%UV%" run st-secretary web
set "RC=%errorlevel%"
rem 75 - обновили исходники из окна программы: запустить заново, уже с новым кодом
if "%RC%"=="75" (
  set "ST_NO_BROWSER=1"
  goto start
)
echo.
if not "%RC%"=="0" (
  echo Программа остановилась с ошибкой. Сообщение выше пригодится разработчикам.
)
echo Это окно можно закрыть.
pause
