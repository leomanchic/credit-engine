"""Independent section-17 audit; run with Python, no credit_engine import.

The integer-power form below is algebraically equivalent to the specification,
but does not call any implementation function or generate fixture expectations.
All expected display values below are literal values from section 17.
"""

from decimal import Context, Decimal, ROUND_HALF_UP, localcontext


def verify() -> None:
    rows = (
        ("2000000", 24, "200000", "105742.19", "105742.30", "2537812.67", "1.372286"),
        ("900000", 24, "200000", "47583.99", "47583.93", "1142015.70", "2.283522"),
        ("400000", 24, "80000", "21148.44", "21148.41", "507562.53", "1.308292"),
        ("1760000", 24, "200000", "93053.13", "93053.16", "2233275.15", "1.503159"),
        ("2000000", 36, "200000", "78465.71", "78465.54", "2824765.39", "1.688252"),
        ("2000000", 12, "200000", "189119.19", "189119.23", "2269430.32", "0.872908"),
    )
    with localcontext(Context(prec=60, rounding=ROUND_HALF_UP)):
        d = Decimal
        rate = d("0.24") / 12
        penny = d(".01")
        for amount, months, cashflow, monthly_ref, last_ref, total_ref, dscr_ref in rows:
            growth = (1 + rate) ** months
            payment = d(amount) * rate * growth / (growth - 1)
            monthly = payment.quantize(penny)
            total = (payment * months).quantize(penny)
            last = total - monthly * (months - 1)
            ratio = (d(cashflow) / (d("40000") + payment)).quantize(d(".000001"))
            actual = (monthly, last, total, ratio)
            expected = tuple(map(d, (monthly_ref, last_ref, total_ref, dscr_ref)))
            assert actual == expected, (amount, months, actual, expected)
            print(f"{amount}/{months}: payment={monthly}; last={last}; total={total}; DSCR={ratio}")

        for cashflow, threshold, months, prefix in (
            ("80000", "1.30", 24, "407376.859142"),
            ("200000", "1.30", 24, "2153277.684040"),
            ("200000", "1.50", 24, "1765299.722952"),
            ("80000", "1.30", 36, "548990.453466"),
        ):
            growth = (1 + rate) ** months
            capacity = max(d(0), d(cashflow) / d(threshold) - d("40000"))
            limit = capacity * (growth - 1) / (rate * growth)
            assert str(limit).startswith(prefix), (cashflow, threshold, months, limit)
            print(f"C={cashflow}, k={threshold}, months={months}: cash_limit={limit}")

        assert d("10000000") * (1 - d(".35")) - d("300000") - d("2000000") == d("4200000")
        assert d("2000000") * (1 - d(".35")) - d("100000") - d("300000") == d("900000")
        print("All section-17 reference values agree with the independent 60-digit calculation.")


if __name__ == "__main__":
    verify()
