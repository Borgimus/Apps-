@echo off
REM ============================================================
REM  ICT Liquidity Sweep - Rebuild & Restart Script (Windows)
REM  Double-click this file, or run "rebuild.bat" in a terminal.
REM ============================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo  Stopping running containers...
echo ============================================================
docker compose down

echo.
echo ============================================================
echo  Rebuilding images (no cache)...
echo ============================================================
docker compose build --no-cache
if errorlevel 1 (
    echo.
    echo  ^!^!  BUILD FAILED - see errors above.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Starting containers...
echo ============================================================
docker compose up -d
if errorlevel 1 (
    echo.
    echo  ^!^!  STARTUP FAILED - see errors above.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Done. Containers running:
echo ============================================================
docker compose ps

echo.
echo  Frontend:  http://localhost:3000/dashboard
echo  Backend:   http://localhost:8000/docs
echo.
pause
