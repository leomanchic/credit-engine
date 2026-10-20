# Автономная установка комплекта на Windows x64

Архив `credit_engine-2.0.0-delivery.zip` содержит исходники, документы, схемы, 42 примера, wheel пакета и каталог `wheelhouse/` с зафиксированными зависимостями для CPython 3.12 / Windows x64. Сам интерпретатор Python в архив не включён.

После распаковки и при уже установленном Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --find-links wheelhouse -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-index --no-deps dist/credit_engine-2.0.0-py3-none-any.whl
.\.venv\Scripts\credit-engine.exe evaluate --snapshot examples/F01/snapshot.json --checks examples/F01/checks.json --policy policies/demo-assets-2.json --out result.json
.\.venv\Scripts\credit-engine.exe export-schemas --out schemas
.\.venv\Scripts\python.exe -I -m pytest -o pythonpath= -q
```

В локальной проверке использован тот же каталог зависимостей по пути `.artifacts/wheelhouse`; установка во второе окружение выполнена с `--no-index`, без сети. Итоги приведены в [VERIFICATION.md](VERIFICATION.md).

`requirements.lock` включает инструменты разработки, чтобы весь комплект тестов запускался без дополнительных загрузок. Для обычного вызова нужны только Pydantic и его четыре транзитивные зависимости. На других ОС нужен соответствующий платформе wheel `pydantic_core` и отдельная проверка установки.

В корне архива находится `SHA256SUMS` для проверки содержимого. Файл с SHA-256 самого архива расположен рядом с ним в каталоге dist.
