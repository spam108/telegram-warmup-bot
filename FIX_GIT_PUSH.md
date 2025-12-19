# Исправление ошибки git push

## Проблема
```
error: src refspec main does not match any
error: failed to push some refs to 'https://github.com/spam108/telegram-warmup-bot.git'
```

## Причина
Локальная ветка называется `master`, а вы пытаетесь запушить в `main`, или нет коммитов.

## Решение

### Вариант 1: Использовать скрипт (рекомендуется)

```cmd
push_to_github.bat
```

Скрипт автоматически:
1. Проверит наличие изменений
2. Добавит файлы и сделает коммит
3. Попытается запушить в `main`, если не получится - в `master`

### Вариант 2: Ручное решение

#### Шаг 1: Проверьте текущую ветку
```cmd
git branch
```

#### Шаг 2: Если ветка `master`, переименуйте в `main` (или пушите в `master`)
```cmd
git branch -m master main
```

#### Шаг 3: Сделайте коммит (если еще не сделали)
```cmd
prepare_commit.bat
git commit -m "Improve bot stability: add error handling, retry mechanisms, and deployment scripts"
```

#### Шаг 4: Запушьте изменения
```cmd
git push -u origin main
```

Или если хотите оставить ветку `master`:
```cmd
git push -u origin master
```

### Вариант 3: Прямой пуш в существующую ветку

Если на GitHub уже есть ветка `master`:
```cmd
git push -u origin master
```

Если на GitHub уже есть ветка `main`:
```cmd
git branch -m master main
git push -u origin main
```

## Проверка

После успешного push проверьте:
```cmd
git remote -v
git branch -a
```

## Настройка GitHub

Если репозиторий на GitHub использует `main` как основную ветку, но у вас локально `master`:

1. Переименуйте локальную ветку:
   ```cmd
   git branch -m master main
   ```

2. Запушьте:
   ```cmd
   git push -u origin main
   ```

3. На GitHub можно удалить старую ветку `master` (если она есть) через веб-интерфейс

