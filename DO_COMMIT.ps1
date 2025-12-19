# PowerShell script to add files and commit

Write-Host "========================================" -ForegroundColor Green
Write-Host "Adding files and committing" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# Add core files
Write-Host "Adding core files..." -ForegroundColor Yellow
git add .gitignore .gitattributes
git add main.py db.py comment_engine.py reaction_engine.py
git add requirements.txt requirements_no_tg.txt

# Add configuration
Write-Host "Adding configuration files..." -ForegroundColor Yellow
git add Dockerfile docker-compose.yml
git add quick_deploy.sh deploy.sh
git add env.example .env.example
git add init_database.sql
git add CDXBOT0310.service

# Add documentation
Write-Host "Adding documentation..." -ForegroundColor Yellow
git add README.md DEPLOYMENT.md QUICK_START.md STABILITY_IMPROVEMENTS.md
git add SERVER_UPDATE_INSTRUCTIONS.md V5_NO_DOCKER_SETUP.md STABLE_V4_WORKING.md
git add DEBUG_INSTRUCTIONS.md datetime_string_analysis.md
git add FIX_GIT_PUSH.md GIT_COMMIT_INSTRUCTIONS.md

# Add scripts
Write-Host "Adding scripts..." -ForegroundColor Yellow
git add init-git.sh init-git.ps1
git add prepare_commit.bat push_to_github.bat add_and_commit.bat SIMPLE_COMMIT.bat DO_COMMIT.ps1
git add fix_mode.py

# Add tests
Write-Host "Adding tests..." -ForegroundColor Yellow
git add test_*.py
git add server_test_settings.py simple_test.py

# Add SQL files
Write-Host "Adding SQL files..." -ForegroundColor Yellow
git add create_all_tables.sql add_missing_tables.sql

# Add other files
Write-Host "Adding other files..." -ForegroundColor Yellow
git add schedule.json
git add update_server.bat update_server_commands.txt

Write-Host ""
Write-Host "Checking status..." -ForegroundColor Yellow
git status --short

Write-Host ""
Write-Host "Committing..." -ForegroundColor Yellow
git commit -m "Improve bot stability: add error handling, retry mechanisms, and deployment scripts"

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "Commit successful!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Now you can push:" -ForegroundColor Cyan
    Write-Host "  git push -u origin main" -ForegroundColor White
} else {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Red
    Write-Host "Commit failed!" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "Check git status:" -ForegroundColor Yellow
    Write-Host "  git status" -ForegroundColor White
}

