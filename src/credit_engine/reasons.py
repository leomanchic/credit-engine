"""Stable, deterministic explanation templates. No generated prose or IO."""
from __future__ import annotations

from .models import Reason

MESSAGES = {
    "COMPANY_INACTIVE": "Подтверждено прекращение деятельности компании",
    "FINANCIAL_PERIOD_MISSING": "Нужен отсутствующий финансовый месяц",
    "FINANCIAL_VALUE_MISSING": "Нужно отсутствующее финансовое значение",
    "FINANCIAL_CONFIRMATION_REQUIRED": "Нужно подтвердить полноту счетов и классификацию расходов",
    "DEBT_UNKNOWN": "Нужно подтвердить полный размер существующих долговых платежей",
    "HISTORY_UNAVAILABLE": "Нужна полная подтверждённая кредитная история за требуемый период",
    "CHECK_EXPIRED": "Срок действия проверки истёк; требуется обновление",
    "CHECK_PROVIDER_UNAVAILABLE": "Проверка завершилась технической ошибкой; требуется повтор",
    "CHECK_PENDING": "Обязательная проверка выполняется",
    "CHECK_MISSING": "Нужны сведения для обязательной проверки",
    "CHECK_REVIEW_REQUIRED": "Результат проверки требует рассмотрения сотрудником",
    "SHORT_OPERATING_HISTORY": "Недостаточно полных месяцев деятельности для автоматического расчёта",
    "SEASONAL_CASHFLOW": "Сезонный поток требует рассмотрения подходящего графика платежей",
    "IRREGULAR_DEBT_SERVICE": "Нерегулярное обслуживание действующего долга требует рассмотрения",
    "ACTIVE_OVERDUE": "Текущая просрочка требует рассмотрения сотрудником",
    "ASSET_DOCUMENTS_REQUIRED": "Нужны документы и принятая оценка актива",
    "ASSET_CHECK_PENDING": "Проверка актива выполняется",
    "ASSET_INELIGIBLE": "Актив исключён из принятого обеспечения",
    "ASSET_REVIEW_REQUIRED": "Пригодность или оценка актива требует решения сотрудника",
    "ADDITIONAL_COLLATERAL_POSSIBLE": "Доступное предложение сохранено; оценка дополнительного актива может увеличить сумму",
    "VALUATION_EXPIRED": "Оценка актива устарела; требуется обновление",
    "ADD_COLLATERAL": "Для выбранного продукта нужно указать обеспечение",
    "CASHFLOW_LIMIT": "Сумму ограничивает подтверждённый денежный поток",
    "CASHFLOW_BELOW_PRODUCT_MIN": "Денежного потока недостаточно для минимальной суммы продукта на всех сроках",
    "COLLATERAL_LIMIT": "Сумму ограничивает принятое обеспечение",
    "COLLATERAL_INSUFFICIENT": "Подтверждённого обеспечения недостаточно для допустимого предложения",
    "PRODUCT_LIMIT": "Действует ограничение суммы выбранного продукта",
    "AMOUNT_STEP_LIMIT": "В пределах подтверждённых лимитов нет уменьшенной суммы с разрешённым шагом",
    "FINANCING_NEED_LIMIT": "Сумма ограничена потребностью в финансировании",
    "SCORE_REVIEW": "Скор допустимых условий требует рассмотрения сотрудником",
    "SCORE_BELOW_MIN": "Скор ниже минимального уровня для рассмотрения",
    "LONGER_TERM_FITS_CAPACITY": "Более долгий срок позволяет уложиться в платёжную ёмкость",
    "LOWER_AMOUNT_IMPROVES_SCORE": "Меньшая сумма повышает покрытие платежей и скор",
    "FUNDING_GAP": "Предложение покрывает потребность в финансировании не полностью",
}


def reason(code: str, *, audience: str = "CLIENT", scope: str = "APPLICATION",
           subject_id: str | None = None, params: dict | None = None,
           source_refs=(), next_action: str | None = None) -> Reason:
    return Reason(code=code, audience=audience, scope=scope, subject_id=subject_id,
                  message_ru=MESSAGES[code], params=params or {},
                  source_refs=tuple(sorted(set(source_refs))), next_action=next_action)
