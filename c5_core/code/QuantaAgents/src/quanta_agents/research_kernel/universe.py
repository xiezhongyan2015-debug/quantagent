"""Shared signal-date selection universe, independent of account execution."""


def execution_pool(panel):
    # Historical observations stay intact for lag/rolling expressions.
    required = {"close", "amount", "is_st", "is_delisting"}
    missing = required - panel.fields.keys()
    if missing:
        raise ValueError("missing selection fields: " + str(sorted(missing)))
    close = panel.fields["close"]
    eligible = (panel.eligible & close.rolling(120, min_periods=120).count().ge(120)
                & panel.fields["amount"].rolling(20, min_periods=20).mean().gt(0))
    for name in ("is_st", "is_delisting"):
        eligible &= panel.fields[name].eq(0)
    return eligible
