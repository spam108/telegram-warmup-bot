@echo off
REM Script to prepare git commit with all necessary files

echo Adding core files...
git add .gitignore .gitattributes
git add main.py db.py comment_engine.py reaction_engine.py
git add requirements.txt requirements_no_tg.txt

echo Adding configuration files...
git add Dockerfile docker-compose.yml docker-compose.yml.backup
git add quick_deploy.sh deploy.sh
git add env.example
git add init_database.sql
git add CDXBOT0310.service

echo Adding documentation...
git add README.md DEPLOYMENT.md QUICK_START.md STABILITY_IMPROVEMENTS.md
git add SERVER_UPDATE_INSTRUCTIONS.md V5_NO_DOCKER_SETUP.md STABLE_V4_WORKING.md
git add DEBUG_INSTRUCTIONS.md datetime_string_analysis.md

echo Adding scripts...
git add init-git.sh init-git.ps1
git add fix_mode.py

echo Adding tests...
git add test_*.py
git add server_test_settings.py simple_test.py

echo Adding SQL files...
git add create_all_tables.sql add_missing_tables.sql

echo.
echo Files staged. Run 'git status' to check, then 'git commit -m "message"'

