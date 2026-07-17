from __future__ import annotations

import itertools
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

from acd_backtest import metrics
from acd_backtest_multiyear import load_xauusd

OUT = Path("artifacts_execution")
OUT.mkdir(exist_ok=True)


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


def _mins(index: pd.DatetimeIndex) -> np.ndarray:
    return index.hour * 60 + index.minute


def _pos_range(day: pd.DataFrame, start: str, end: str) -> np.ndarray:
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    mins = _mins(day.index)
    return np.flatnonzero((mins >= sh * 60 + sm) & (mins < eh * 60 + em))


def prior_atr(atr: pd.Series, d) -> float | None:
    s = atr.loc[atr.index < d]
    return None if s.empty else float(s.iloc[-1])


def find_entry(day: pd.DataFrame, positions: np.ndarray, level: float, atr: float, mode: str):
    if len(positions) == 0:
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
                entry_pos = int(pos) + 1
                if entry_pos >= len(day) or entry_pos > int(positions[-1]):
                    return None
                return entry_pos, float(day.iloc[entry_pos].open), float(row.low)
        return None

    if mode == "retest":
        breakout_pos = None
        for pos in positions:
            row = day.iloc[pos]
            if breakout_pos is None:
                if row.close > level and row.close > row.ema200:
                    breakout_pos = int(pos)
                continue
            if int(pos) > breakout_pos + 6:
                return None
            # Retest must touch a small band around A and close back above it.
            if row.low <= level + atr * 0.01 and row.close > level and row.close > row.ema200:
                entry_pos = int(pos) + 1
                if entry_pos >= len(day) or entry_pos > int(positions[-1]):
                    return None
                return entry_pos, float(day.iloc[entry_pos].open), float(row.low)
        return None

    raise ValueError(mode)


def simulate_trade(day: pd.DataFrame, entry_pos: int, last_pos: int, entry: float, stop: float, rr: float, be_r, cost: float, d: str):
    risk = entry - stop
    if not np.isfinite(risk) or risk <= 0:
        return None
    target = entry + risk * rr
    active_stop = stop
    be_armed = False

    exit_price = float(day.iloc[last_pos].close)
    exit_pos = last_pos
    reason = "TIME"

    for pos in range(entry_pos, last_pos + 1):
        row = day.iloc[pos]
        stop_hit = row.low <= active_stop
        target_hit = row.high >= target

        # Conservative intrabar assumption: stop before target, and no same-bar
        # breakeven activation.
        if stop_hit:
            exit_price = active_stop
            exit_pos = pos
            reason = "BE" if active_stop >= entry else "STOP"
            break
        if target_hit:
            exit_price = target
            exit_pos = pos
            reason = "TP"
            break

        if be_r is not None and not be_armed and row.high >= entry + risk * be_r:
            be_armed = True
        elif be_armed:
            active_stop = max(active_stop, entry)

    raw = exit_price - entry
    net = raw - cost
    return ExecTrade(
        date=d,
        entry_time=day.index[entry_pos].isoformat(),
        exit_time=day.index[exit_pos].isoformat(),
        entry=float(entry),
        exit=float(exit_price),
        stop=float(stop),
        target=float(target),
        risk=float(risk),
        net_points=float(net),
        r_net=float(net / risk),
        exit_reason=reason,
    )


def backtest(intraday: pd.DataFrame, atr: pd.Series, cfg: ExecConfig):
    trades = []
    for d, day in intraday.groupby(intraday.index.date):
        # Tue-Fri only, fixed from the prior untouched-year filter stage.
        if pd.Timestamp(d).weekday() not in {1, 2, 3, 4}:
            continue
        day = day.sort_index()
        da = prior_atr(atr, d)
        if da is None or da <= 0:
            continue

        or_pos = _pos_range(day, "09:30", "09:45")
        if len(or_pos) < 2:
            continue
        orb = day.iloc[or_pos]
        or_high = float(orb.high.max())
        or_low = float(orb.low.min())
        or_mid = (or_high + or_low) / 2.0
        ratio = (or_high - or_low) / da
        if not 0.05 <= ratio <= 0.35:
            continue

        entry_positions = _pos_range(day, "09:45", "12:00")
        level = or_high + da * cfg.a_mult
        found = find_entry(day, entry_positions, level, da, cfg.entry_mode)
        if found is None:
            continue
        entry_pos, entry, signal_low = found

        if cfg.stop_mode == "far":
            stop = or_low
        elif cfg.stop_mode == "mid":
            stop = or_mid
        elif cfg.stop_mode == "signal_low":
            stop = min(signal_low, entry - day.iloc[entry_pos].close * 0 + 1e-9)
        else:
            raise ValueError(cfg.stop_mode)

        exit_positions = _pos_range(day, "09:45", cfg.force_exit)
        if len(exit_positions) == 0:
            continue
        last_pos = int(exit_positions[-1])
        if entry_pos > last_pos:
            continue

        trade = simulate_trade(day, entry_pos, last_pos, entry, stop, cfg.rr, cfg.breakeven_r, cfg.cost_points, str(d))
        if trade is not None:
            trades.append(trade)
    return trades


def pm(trades, y0=None, y1=None):
    selected = []
    for t in trades:
        y = int(t.date[:4])
        if y0 is not None and y < y0:
            continue
        if y1 is not None and y > y1:
            continue
        selected.append(t)
    return metrics(selected)


def main():
    intraday, atr = load_xauusd()
    configs = [
        ExecConfig(a, em, sm, rr, be, fx)
        for a, em, sm, rr, be, fx in itertools.product(
            [0.03, 0.05, 0.08],
            ["touch", "close1", "close2", "retest"],
            ["far", "mid", "signal_low"],
            [0.75, 1.0, 1.25, 1.5, 2.0],
            [None, 0.5, 0.75, 1.0],
            ["12:00", "14:00", "16:00"],
        )
    ]

    rows = []
    for i, cfg in enumerate(configs, 1):
        trades = backtest(intraday, atr, cfg)
        row = asdict(cfg)
        row.update({f"disc_{k}": v for k, v in pm(trades, None, 2021).items()})
        row.update({f"y2022_{k}": v for k, v in pm(trades, 2022, 2022).items()})
        row.update({f"y2023_{k}": v for k, v in pm(trades, 2023, 2023).items()})
        row.update({f"all_{k}": v for k, v in metrics(trades).items()})
        rows.append(row)
        if i % 300 == 0:
            print(f"processed {i}/{len(configs)}")

    df = pd.DataFrame(rows)
    df["disc_score"] = (
        df.disc_avg_r.clip(lower=-1, upper=2) * np.sqrt(df.disc_trades.clip(lower=0))
        - 0.025 * df.disc_max_dd_r
    )
    df.to_csv(OUT / "execution_grid.csv", index=False)

    ranked = df[
        (df.disc_trades >= 50)
        & np.isfinite(df.disc_profit_factor)
        & (df.disc_profit_factor > 1.0)
        & (df.disc_avg_r > 0.0)
    ].sort_values(["disc_score", "disc_profit_factor"], ascending=False)
    ranked.head(50).to_csv(OUT / "execution_top50_discovery.csv", index=False)

    robust = ranked[
        (ranked.y2022_trades >= 20)
        & (ranked.y2023_trades >= 15)
        & (ranked.y2022_profit_factor > 1.0)
        & (ranked.y2023_profit_factor > 1.0)
        & (ranked.y2022_avg_r > 0.0)
        & (ranked.y2023_avg_r > 0.0)
    ].copy()
    robust.to_csv(OUT / "execution_validation_positive.csv", index=False)

    strict = robust[
        (robust.all_profit_factor >= 1.35)
        & (robust.all_avg_r >= 0.15)
        & (robust.all_max_dd_r <= 5.0)
    ].copy()
    strict.to_csv(OUT / "execution_strict_pass.csv", index=False)

    cols = [
        "a_mult", "entry_mode", "stop_mode", "rr", "breakeven_r", "force_exit",
        "disc_trades", "disc_profit_factor", "disc_avg_r", "disc_max_dd_r",
        "y2022_trades", "y2022_profit_factor", "y2022_avg_r",
        "y2023_trades", "y2023_profit_factor", "y2023_avg_r",
        "all_trades", "all_win_rate", "all_profit_factor", "all_avg_r", "all_max_dd_r",
    ]
    lines = [
        "# ACD Execution Optimization",
        "",
        "Fixed before this stage: Tue-Fri, long-only, NY 09:30-09:45 OR, EMA200, no C, OR/ATR 0.05-0.35.",
        "Discovery: through 2021. Untouched validations: 2022 and 2023.",
        "Same-bar stop/target is counted as stop; breakeven activates no earlier than the next bar.",
        "",
        "## Top 15 selected only on discovery",
        "",
        ranked.head(15)[cols].to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Positive in both validation years",
        "",
        f"Count: {len(robust)}",
        "",
        robust.head(30)[cols].to_markdown(index=False, floatfmt=".3f") if len(robust) else "None.",
        "",
        "## Strict improvement over v1.2",
        "",
        f"Count: {len(strict)}",
        "",
        strict.head(30)[cols].to_markdown(index=False, floatfmt=".3f") if len(strict) else "None.",
    ]
    (OUT / "execution_report.md").write_text("\n".join(lines), encoding="utf-8")
    print((OUT / "execution_report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
