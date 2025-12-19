@echo off
REM Script to commit and push changes to GitHub

echo ========================================
echo Preparing commit and push to GitHub
echo ========================================
echo.

REM Check if there are uncommitted changes
git status --short >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Not a git repository or git not found
    pause
    exit /b 1
)

REM Check current branch
for /f "tokens=2" %%b in ('git branch --show-current 2^>nul') do set CURRENT_BRANCH=%%b
if "%CURRENT_BRANCH%"=="" (
    for /f "tokens=2*" %%b in ('git branch 2^>nul ^| findstr /C:"*"') do set CURRENT_BRANCH=%%b
)

echo Current branch: %CURRENT_BRANCH%
echo.

REM Check if there are changes to commit
git diff --quiet --exit-code
if %errorlevel% neq 0 (
    echo There are uncommitted changes.
    echo.
    echo Adding files...
    call prepare_commit.bat
    
    echo.
    echo Committing changes...
    git commit -m "Improve bot stability: add error handling, retry mechanisms, and deployment scripts"
    
    if %errorlevel% neq 0 (
        echo Error: Commit failed
        pause
        exit /b 1
    )
    echo Commit successful!
) else (
    echo No uncommitted changes found.
)

echo.
echo Checking remote...
git remote -v >nul 2>&1
if %errorlevel% neq 0 (
    echo No remote configured. Please add remote first:
    echo   git remote add origin https://github.com/spam108/telegram-warmup-bot.git
    pause
    exit /b 1
)

echo.
echo Current branch: %CURRENT_BRANCH%
echo.

REM Try to push to main first, if fails try master
echo Attempting to push to 'main' branch...
git push -u origin %CURRENT_BRANCH%:main 2>nul
if %errorlevel% equ 0 (
    echo Successfully pushed to main branch!
    echo.
    echo You may want to rename your local branch:
    echo   git branch -m master main
    goto :end
)

echo Push to 'main' failed, trying 'master'...
git push -u origin %CURRENT_BRANCH%:master 2>nul
if %errorlevel% equ 0 (
    echo Successfully pushed to master branch!
    goto :end
)

echo.
echo Push failed. Possible solutions:
echo.
echo 1. If your local branch is 'master' but remote uses 'main':
echo    git branch -m master main
echo    git push -u origin main
echo.
echo 2. If remote uses 'master':
echo    git push -u origin master
echo.
echo 3. Check if you have commits:
echo    git log --oneline -5
echo.
echo 4. Check remote configuration:
echo    git remote -v
echo.

:end
echo.
pause

