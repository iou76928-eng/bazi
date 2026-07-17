from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from acd_backtest import metrics


@dataclass(frozen=True)
class ExecConfig:
    a_mult: float
    entry_mode: str
    stop_mode: str
    rr: float
    breakeven_r: float | None
    force_exit: str
    cost_points: float = 0.25


@dataclass
class ExecTrade:
    date: str
    entry_time: str
    exit_time: str
    entry: float
    exit: float
    stop: float
    target: float
    risk: float
    net_points: float
    r_net: float
    exit_reason: str


@dataclass
class DayContext:
    date: str
    year: int
    day: pd.DataFrame
    daily_atr: float
    or_high: float
    or_low: float
    or_mid: float
    entry_positions: np.ndarray
    last_1200: int | None
    last_1600: int | None


def _mins(index: pd.DatetimeIndex) -> np.ndarray:
    return index.hour * 60 + index.minute


def _positions(index: pd.DatetimeIndex, start: str, end: str) -> np.ndarray:
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    mins = _mins(index)
    return np.flatnonzero((mins >= sh * 60 + sm) & (mins < eh * 60 + em))


def prepare_days(intraday: pd.DataFrame, atr: pd.Series) -> list[DayContext]:
    atr_dates = np.array(list(atr.index), dtype=object)
    atr_values = atr.to_numpy(dtype=float)
    contexts: list[DayContext] = []

    for d, day in intraday.groupby(intraday.index.date, sort=True):
        if pd.Timestamp(d).weekday() not in {1, 2, 3, 4}:
            continue
        idx = int(np.searchsorted(atr_dates, d, side="left")) - 1
        if idx < 0:
            continue
        da = float(atr_values[idx])
        if not np.isfinite(da) or da <= 0:
            continue

        day = day.sort_index()
        or_pos = _positions(day.index, "09:30", "09:45")
        if len(or_pos) < 2:
            continue
        orb = day.iloc[or_pos]
        high = float(orb.high.max())
        low = float(orb.low.min())
        ratio = (high - low) / da
        if not 0.05 <= ratio <= 0.35:
            continue

        entries = _positions(day.index, "09:45", "12:00")
        p12 = _positions(day.index, "09:45", "12:00")
        p16 = _positions(day.index, "09:45", "16:00")
        contexts.append(DayContext(
            date=str(d),
            year=int(str(d)[:4]),
            day=day,
            daily_atr=da,
            or_high=high,
            or_low=low,
            or_mid=(high + low) / 2.0,
            entry_positions=entries,
            last_1200=int(p12[-1]) if len(p12) else None,
            last_1600=int(p16[-1]) if len(p16) else None,
        ))
    return contexts


def find_entry(ctx: DayContext, level: float, mode: str):
    day = ctx.day
    positions = ctx.entry_positions
    if not len(positions):
        return None

    if mode == "touch":
        for pos in positions:
            row = day.iloc[pos]
            if row.high >= level and row.close > row.ema200:
                return int(pos), float(level), float(row.low)
        return None

    if mode in {"close1", "close2"}:
        needed = 1 if mode == "close1" else 2
        streak = 0
        for pos in positions:
            row = day.iloc[pos]
            streak = streak + 1 if row.close > level and row.close > row.ema200 else 0
            if streak >= needed:
                ep = int(pos) + 1
                if ep >= len(day) or ep > int(positions[-1]):
                    return None
                return ep, float(day.iloc[ep].open), float(row.low)
        return None

    if mode == "retest":
        breakout = None
        for pos in positions:
            row = day.iloc[pos]
            if breakout is None:
                if row.close > level and row.close > row.ema200:
                    breakout = int(pos)
                continue
            if int(pos) > breakout + 6:
                return None
            if row.low <= level + ctx.daily_atr * 0.01 and row.close > level and row.close > row.ema200:
                ep = int(pos) + 1
                if ep >= len(day) or ep > int(positions[-1]):
                    return None
                return ep, float(day.iloc[ep].open), float(row.low)
        return None
    raise ValueError(mode)


def simulate(ctx: DayContext, cfg: ExecConfig, entry_pos: int, entry: float, signal_low: float):
    if cfg.stop_mode == "far":
        stop = ctx.or_low
    elif cfg.stop_mode == "mid":
        stop = ctx.or_mid
    elif cfg.stop_mode == "signal_low":
        stop = min(signal_low, entry - 1e-9)
    else:
        raise ValueError(cfg.stop_mode)

    risk = entry - stop
    if not np.isfinite(risk) or risk <= 0:
        return None
    target = entry + risk * cfg.rr
    last_pos = ctx.last_1200 if cfg.force_exit == "12:00" else ctx.last_1600
    if last_pos is None or entry_pos > last_pos:
        return None

    active_stop = stop
    be_armed = False
    exit_price = float(ctx.day.iloc[last_pos].close)
    exit_pos = last_pos
    reason = "TIME"

    for pos in range(entry_pos, last_pos + 1):
        row = ctx.day.iloc[pos]
        # Conservative: stop is assumed first when both are touched.
        if row.low <= active_stop:
            exit_price = active_stop
            exit_pos = pos
            reason = "BE" if active_stop >= entry else "STOP"
            break
        if row.high >= target:
            exit_price = target
            exit_pos = pos
            reason = "TP"
            break
        if cfg.breakeven_r is not None and not be_armed and row.high >= entry + risk * cfg.breakeven_r:
            be_armed = True
        elif be_armed:
            active_stop = max(active_stop, entry)

    net = exit_price - entry - cfg.cost_points
    return ExecTrade(
        date=ctx.date,
        entry_time=ctx.day.index[entry_pos].isoformat(),
        exit_time=ctx.day.index[exit_pos].isoformat(),
        entry=float(entry),
        exit=float(exit_price),
        stop=float(stop),
        target=float(target),
        risk=float(risk),
        net_points=float(net),
        r_net=float(net / risk),
        exit_reason=reason,
    )


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


def period_metrics(trades, y0=None, y1=None):
    selected = [
        t for t in trades
        if (y0 is None or int(t.date[:4]) >= y0)
        and (y1 is None or int(t.date[:4]) <= y1)
    ]
    return metrics(selected)
