# Literature opportunity monitor

Ежедневно читает `Sheet1` таблицы **Journals and platforms**, проверяет сайты из
колонки B и обновляет только:

- F — OpenCall;
- G — Awards;
- H — Submissions.

При недоступном сайте или неоднозначных доказательствах текущие значения F:H
сохраняются. После записи значения повторно читаются для проверки.

Между запусками сохраняется SHA-256-хеш прочитанного текста каждого сайта.
Если хеш не изменился, сайт не отправляется на повторный AI-анализ.

Подготовленный Telegram-отчёт сначала сохраняется в `liter/.cache`. После
успешной отправки сообщения удаляются из очереди по одному. Если Telegram
недоступен, следующий запланированный запуск продолжает отправку с первого
неотправленного сообщения, не перечитывая сайты и не вызывая OpenAI.

## Настройка

Добавьте в **Settings → Secrets and variables → Actions**:

| Secret | Обязателен | Назначение |
|---|---:|---|
| `OPENAI_API_KEY` | да | Анализ страниц через OpenAI API |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | да | Полный JSON сервисного аккаунта Google |
| `TELEGRAM_BOT_TOKEN` | нет | Токен Telegram-бота |
| `TELEGRAM_CHAT_ID` | нет | Пользователь, группа или канал для отчёта |

Поделитесь Google Sheet с `client_email` из сервисного аккаунта, предоставив
право редактирования.

Опциональные repository variables:

- `OPENAI_MODEL` — по умолчанию `gpt-5.6-luna`;
- `GOOGLE_SPREADSHEET_ID` — уже настроен на текущую таблицу;
- `GOOGLE_SHEET_NAME` — по умолчанию `Sheet1`;
- `FETCH_WORKERS` — параллельные загрузки, по умолчанию `8`.

## Запуск

Workflow можно запустить вручную во вкладке **Actions**. Встроенный GitHub
cron запускает его один раз в день и остаётся резервным планировщиком.

Внешний планировщик должен вызывать `workflow_dispatch` с параметром
`scheduled_run: true`. Такие вызовы используют ту же дневную отметку: первый
успешный запуск проверяет сайты, а последующие в тот же день только доставляют
неотправленный Telegram-отчёт либо сразу завершаются. Параллельные запуски
монитора выполняются последовательно.

Для Google Cloud Scheduler используйте HTTP `POST` каждые шесть часов:

- URL: `https://api.github.com/repos/agehsbargswork-blip/scrappers/actions/workflows/liter-daily.yml/dispatches`;
- заголовки: `Authorization: Bearer <GITHUB_TOKEN>`,
  `Accept: application/vnd.github+json`, `Content-Type: application/json`;
- тело: `{"ref":"main","inputs":{"scheduled_run":true}}`.

Fine-grained GitHub token достаточно ограничить этим репозиторием и разрешением
**Actions: write**. В Cloud Scheduler следует включить до пяти повторов при
временной ошибке HTTP.

Локально:

```bash
python -m pip install -r liter/requirements.txt
python liter/src/check_sites.py
```

## Граница проверки

Монитор читает URL из таблицы и до четырёх релевантных внутренних ссылок,
найденных на этой странице. Это не полный обход сайта. JavaScript-only страницы
будут указаны в отчёте как требующие проверки; поддержка Playwright может быть
добавлена отдельно.
