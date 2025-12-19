@echo off
REM Simple script to add all necessary files and commit

echo Adding core files...
git add .gitignore .gitattributes
git add main.py db.py comment_engine.py reaction_engine.py
git add requirements.txt requirements_no_tg.txt

echo Adding configuration files...
git add Dockerfile docker-compose.yml
git add quick_deploy.sh deploy.sh
git add env.example .env.example
git add init_database.sql
git add CDXBOT0310.service

echo Adding documentation...
git add README.md DEPLOYMENT.md QUICK_START.md STABILITY_IMPROVEMENTS.md
git add SERVER_UPDATE_INSTRUCTIONS.md V5_NO_DOCKER_SETUP.md STABLE_V4_WORKING.md
git add DEBUG_INSTRUCTIONS.md datetime_string_analysis.md
git add FIX_GIT_PUSH.md GIT_COMMIT_INSTRUCTIONS.md

echo Adding scripts...
git add init-git.sh init-git.ps1
git add prepare_commit.bat push_to_github.bat add_and_commit.bat
git add fix_mode.py

echo Adding tests...
git add test_*.py
git add server_test_settings.py simple_test.py

echo Adding SQL files...
git add create_all_tables.sql add_missing_tables.sql

echo Adding other files...
git add schedule.json
git add update_server.bat update_server_commands.txt

echo.
echo Checking status...
git status --short

echo.
echo Files added. Ready to commit.
echo Run: git commit -m "Improve bot stability: add error handling, retry mechanisms, and deployment scripts"

