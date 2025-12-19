@echo off
REM One-step commit script

echo ========================================
echo Adding all files and committing
echo ========================================
echo.

call add_and_commit.bat

echo.
echo Committing...
git commit -m "Improve bot stability: add error handling, retry mechanisms, and deployment scripts"

if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo Commit successful!
    echo ========================================
    echo.
    echo Now you can push:
    echo   git push -u origin master
    echo   OR
    echo   git branch -m master main
    echo   git push -u origin main
) else (
    echo.
    echo ========================================
    echo Commit failed!
    echo ========================================
    echo.
    echo Check git status:
    echo   git status
)

pause

