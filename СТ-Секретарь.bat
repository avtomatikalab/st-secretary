@echo off
rem ST-Secretary launcher: double-click to open the program in the browser.
rem Competitions are stored in the "данные" folder next to this file.
rem This file is saved in CP866 (the Russian Windows console code page) so that messages display correctly.
cd /d "%~dp0"
title СТ-Секретарь

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
"%UV%" run st-secretary web
if errorlevel 1 (
  echo.
  echo Программа остановилась с ошибкой. Сообщение выше пригодится разработчикам.
  pause
)
