EXPECTED_TABLES = {
    "account_snapshots",
    "backtest_runs",
    "decisions",
    "fills",
    "iv_history",
    "market_history_cache",
    "opportunities",
    "opportunity_legs",
    "pea_reports",
    "orders",
    "position_events",
    "position_legs",
    "position_marks",
    "positions",
    "screener_runs",
    "shadow_outcomes",
    "strategy_configs",
    "strategy_profiles",
}


def test_migration_creates_every_table(db_inspector) -> None:
    assert EXPECTED_TABLES <= set(db_inspector.get_table_names())


def test_money_columns_are_exact_decimals(db_inspector) -> None:
    columns = {c["name"]: c["type"] for c in db_inspector.get_columns("positions")}

    assert columns["entry_credit"].scale == 4
    assert columns["realized_pnl"].asdecimal


def test_a_position_can_hold_option_and_stock_legs(db_inspector) -> None:
    enums = {e["name"]: e["labels"] for e in db_inspector.get_enums()}

    assert enums["instrument_type"] == ["option", "stock"]
    assert "put_credit_spread" in enums["strategy_type"]
