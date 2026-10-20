# credit_engine

Независимая библиотека демонстрационного кредитного решения по ТЗ 2.0. Публичный вызов `evaluate(snapshot, checks, policy)` не обращается к сети, файлам, БД, времени, OCR или LLM. Он возвращает рекомендацию, объяснения и рассчитанные варианты кредита. Все примеры синтетические; политика не описывает правила реального банка.

## Установка и запуск

Проверяемое окружение: CPython 3.12.14, Windows x64. Точные версии всех зависимостей зафиксированы в `requirements.lock`. Для собственного Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe docs/verify_reference_calculations.py
.\.venv\Scripts\credit-engine.exe evaluate --snapshot examples/F01/snapshot.json --checks examples/F01/checks.json --policy policies/demo-assets-2.json --out result.json
.\.venv\Scripts\credit-engine.exe export-schemas --out schemas
.\.venv\Scripts\python.exe -m build --no-isolation
```

На Linux/macOS используются `.venv/bin/python` и `.venv/bin/credit-engine`. Эти платформы отдельно не проверялись. Расчёт установленного пакета не требует внешних сервисов или API-ключей. Первичная установка зависимостей требует PyPI либо заранее подготовленного каталога wheel.

## Вызов из Python backend

```python
from credit_engine import evaluate, canonical_json
from credit_engine.models import ApplicationSnapshot, NormalizedChecks
from credit_engine.policy import load_policy

snapshot = ApplicationSnapshot.model_validate(snapshot_data)
checks = NormalizedChecks.model_validate(trusted_checks_data)
policy = load_policy("policies/demo-assets-2.json")  # до evaluate
result = evaluate(snapshot, checks, policy)
saved_json = canonical_json(result)
```

Исполнимый локальный пример: `python examples/backend_adapter.py`. Он читает синтетические сохранённые входы и вызывает настоящий пакет. Инструкция для full-stack и обязательные сквозные проверки находятся в [docs/INTEGRATION.md](docs/INTEGRATION.md).

`evaluate` также принимает словари как удобство Python-интеграции и повторно валидирует все входы, включая уже построенные DTO. `InputValidationError.errors` и `PolicyValidationError.errors` содержат безопасные `path`, `code`, `message_ru`. Ошибка политики — отдельный тип, проверяемый прежде ошибки входа. Корректные `DECLINED`, `NEEDS_INFO`, `WAITING_CHECKS`, `MANUAL_REVIEW` являются бизнес-результатами, а не исключениями.

CLI завершает работу с кодом 0 для любого бизнес-результата, 2 для ошибки входа, 3 для ошибки политики, 1 для неожиданной технической ошибки. Диагностика направляется в stderr; результат JSON — в `--out` или stdout.

## Контракт и расчёты

Деньги JSON — строки с ровно двумя десятичными знаками в диапазоне NUMERIC(18,2); `float`, JSON-числа, NaN и бесконечность отклоняются. В Python допустим `Decimal`. `null` обозначает неизвестность, `"0.00"` — ноль. Неизвестные поля отклоняются. Схемы snapshot/checks/policy/result генерируются из Pydantic 2; межполевые правила дополнительно проверяются моделями и `evaluate`.

Поток `C` — минимум средних за 12 и последние 3 полных календарных месяца. Доход активов повторно не добавляется. Оценка DSCR использует неокруглённые значения и неравенство `C >= k * (D + P)`. Каждая сумма и срок получают собственный скор. Все денежные операции используют независимый локальный Decimal-контекст: в демонстрационной политике 50 значащих цифр, поддерживаемый минимум 34.

Первые `n−1` платежей равны отображаемому `monthly_payment`; последний корректирует сумму до `total_payment`. Это демонстрационный график без комиссий, календаря и ПСК. `cash_limit` всегда рассчитан при минимальном DSCR продукта (U01); более строгий лимит для улучшения скора находится в `LOWER_AMOUNT_IMPROVES_SCORE.params.score_cash_limit` вместе с `dscr_threshold`.

Порядок общих решений: структурная ошибка; подтверждённая неактивность; недостающие сведения; ожидание проверок; установленные причины ручного рассмотрения; подбор условий. Неопределённый дополнительный актив не скрывает доступное автоматическое предложение. Для обеспеченного продукта оценка не запрашивается, когда подтверждённая финансовая часть уже исключает любой допустимый вариант.

Для A01 `window_start/window_end` означают покрытие сведений источником, а не срок существования кредитов. Требуется интервал от даты расчёта минус 12 календарных месяцев до предыдущего дня включительно. Частичное окно завершённой проверки ведёт к `WAITING_CHECKS` и `REQUEST_FULL_HISTORY`, без изменения входов. Для молодой компании сохраняется отдельный маршрут короткой истории деятельности.

Канонический JSON: UTF-8, ключи по алфавиту, разделители `,` и `:`, деньги — два знака, коэффициенты — без экспоненты и лишних конечных нулей. Сортируются месяцы, активы, проверки активов, ссылки, продукты, сроки и таблица дисконтов; ранжированный список предложений сохраняет порядок. SHA-256 служит для сопоставления содержания и не подтверждает подлинность документов.

## Комплект и статус

- `src/credit_engine/` — расчёты, контракт, политика, подбор, решения, сериализация и CLI.
- `policies/demo-assets-2.json` — полная фиксированная политика; `schemas/` — экспорт контракта.
- `examples/F01`–`F17`, `F04b` — полные входы и независимые ключевые ожидания, включая варианты сценариев.
- `tests/` — приёмка, числовые эталоны, границы, свойства и CLI.
- [docs/SPEC_REVIEW.md](docs/SPEC_REVIEW.md) — U01, A01 и технические решения.
- [docs/TRACEABILITY.md](docs/TRACEABILITY.md) — требования, реализация и подтверждающие тесты.
- [MODEL_CARD.md](MODEL_CARD.md) — назначение и ограничения модели правил.
- [docs/VERIFICATION.md](docs/VERIFICATION.md) — фактически выполненные команды и результаты проверки поставки.

Исходный `credit_engine_spec.docx` оставлен без изменений. Код настоящего сайта в рабочей папке отсутствует, поэтому сквозная интеграция с его БД, ревизиями и публикацией предложений не подтверждена.
