# Инструкции по коммиту изменений

## Подготовка к коммиту

Я обновил `.gitignore` чтобы исключить временные файлы отладки. Теперь выполните следующие команды:

### Вариант 1: Использование скрипта (Windows)

```cmd
prepare_commit.bat
git status
git commit -m "Improve bot stability: add error handling, retry mechanisms, and deployment scripts"
```

### Вариант 2: Ручное добавление файлов

```bash
# Основные файлы
git add .gitignore .gitattributes
git add main.py db.py comment_engine.py reaction_engine.py
git add requirements.txt

# Конфигурация и развертывание
git add Dockerfile docker-compose.yml
git add quick_deploy.sh deploy.sh env.example
git add init_database.sql CDXBOT0310.service

# Документация
git add README.md DEPLOYMENT.md QUICK_START.md STABILITY_IMPROVEMENTS.md

# Скрипты и тесты
git add init-git.sh init-git.ps1 fix_mode.py
git add test_*.py server_test_settings.py simple_test.py

# SQL файлы
git add create_all_tables.sql add_missing_tables.sql

# Проверка
git status

# Коммит
git commit -m "Improve bot stability: add error handling, retry mechanisms, and deployment scripts"
```

## Что будет исключено из коммита

Следующие файлы автоматически исключены через `.gitignore`:
- Все файлы `fix_*.py` (временные скрипты исправлений)
- Все файлы `add_debug*.py` (отладочные скрипты)
- Backup файлы (`*.backup`, `*.stable-*-backup`, `*_fixed.py`)
- Логи и временные файлы
- Файлы `.env` (но `env.example` будет включен)

## Рекомендуемое сообщение коммита

```
Improve bot stability: add error handling, retry mechanisms, and deployment scripts

- Enhanced error handling in account processing loops
- Added retry mechanisms for critical operations (comments, reactions, channel joins)
- Improved logging with structured output
- Created quick deployment script (quick_deploy.sh)
- Added comprehensive documentation (QUICK_START.md, STABILITY_IMPROVEMENTS.md)
- Updated .gitignore to exclude temporary debug files
```

## После коммита

Если вы используете удаленный репозиторий:

```bash
git push origin master
# или
git push origin main
```

