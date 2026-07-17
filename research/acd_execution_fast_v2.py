from __future__ import annotations

from acd_execution_fast import (
    ExecConfig,
    ExecTrade,
    DayContext,
    prepare_days,
    simulate,
    period_metrics,
)


def find_entry(ctx: DayContext, level: float, mode: str):
    day = ctx.day
    positions = ctx.entry_positions
    if not len(positions):
        return None

    if mode == "touch":
        # A historical Pine stop order is submitted after a completed bar and
        # becomes active on the next bar. Therefore the trend filter must use
        # the previous completed bar, and the first 09:45 bar cannot fill it.
        for pos in positions:
            pos = int(pos)
            if pos <= int(positions[0]):
                continue
            prev = day.iloc[pos - 1]
            row = day.iloc[pos]
            if prev.close > prev.ema200 and row.high >= level:
                # A gap through a buy-stop fills at the bar open, not at the
                # stale stop price.
                entry = max(float(level), float(row.open))
                return pos, entry, float(row.low)
        return None

    if mode in {"close1", "close2"}:
        needed = 1 if mode == "close1" else 2
        streak = 0
        for pos in positions:
            pos = int(pos)
            row = day.iloc[pos]
            streak = streak + 1 if row.close > level and row.close > row.ema200 else 0
            if streak >= needed:
                ep = pos + 1
                if ep >= len(day) or ep > int(positions[-1]):
                    return None
                return ep, float(day.iloc[ep].open), float(row.low)
        return None

    if mode == "retest":
        breakout = None
        for pos in positions:
            pos = int(pos)
            row = day.iloc[pos]
            if breakout is None:
                if row.close > level and row.close > row.ema200:
                    breakout = pos
                continue
            if pos > breakout + 6:
                return None
            if row.low <= level + ctx.daily_atr * 0.01 and row.close > level and row.close > row.ema200:
                ep = pos + 1
                if ep >= len(day) or ep > int(positions[-1]):
                    return None
                return ep, float(day.iloc[ep].open), float(row.low)
        return None

    raise ValueError(mode)


def backtest_days(days: list[DayContext], cfg: ExecConfig):
    trades = []
    for ctx in days:
        level = ctx.or_high + ctx.daily_atr * cfg.a_mult
        found = find_entry(ctx, level, cfg.entry_mode)
        if found is None:
            continue
        trade = simulate(ctx, cfg, *found)
        if trade is not None:
            trades.append(trade)
    return trades
