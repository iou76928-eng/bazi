from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import yfinance as yf

NY = "America/New_York"
OUT = Path("artifacts")
OUT.mkdir(exist_ok=True)


def flatten_yf(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]).lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    return df.rename(columns={"adj close": "adj_close"})


def download_data(ticker: str = "GC=F") -> tuple[pd.DataFrame, pd.Series]:
    intraday = yf.download(
        ticker,
        period="60d",
        interval="5m",
        auto_adjust=False,
        prepost=True,
        progress=False,
        threads=False,
    )
    intraday = flatten_yf(intraday)
    if intraday.empty:
        raise RuntimeError("No 5-minute data downloaded")
    intraday = intraday[["open", "high", "low", "close", "volume"]].dropna(
        subset=["open", "high", "low", "close"]
    )
    idx = pd.DatetimeIndex(intraday.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    intraday.index = idx.tz_convert(NY)
    intraday = intraday[~intraday.index.duplicated(keep="last")].sort_index()
    intraday["ema200"] = intraday["close"].ewm(span=200, adjust=False).mean()

    daily = yf.download(
        ticker,
        period="1y",
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    daily = flatten_yf(daily)
    if daily.empty:
        raise RuntimeError("No daily data downloaded")
    daily = daily[["high", "low", "close"]].dropna()
    prev_close = daily["close"].shift(1)
    tr = pd.concat(
        [
            daily["high"] - daily["low"],
            (daily["high"] - prev_close).abs(),
            (daily["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean()
    atr.index = pd.Index([pd.Timestamp(x).date() for x in atr.index])
    return intraday, atr.dropna()


@dataclass(frozen=True)
class Config:
    session_name: str
    or_start: str
    or_end: str
    a_cutoff: str
    c_cutoff: str
    a_mult: float
    c_mult: float
    stop_mode: str
    rr: float
    enable_c: bool
    trend_filter: str
    cost_points: float = 0.25
    min_or_atr: float = 0.03
    max_or_atr: float = 0.35
    c_delay_bars: int = 1


@dataclass
class Trade:
    date: str
    leg: str
    side: str
    entry_time: str
    exit_time: str
    entry: float
    exit: float
    stop: float
    target: float
    risk: float
    raw_points: float
    net_points: float
    r_net: float
    exit_reason: str


def hhmm(s: str) -> tuple[int, int]:
    return int(s[:2]), int(s[3:])


def time_mask(index: pd.DatetimeIndex, start: str, end: str) -> np.ndarray:
    sh, sm = hhmm(start)
    eh, em = hhmm(end)
    mins = index.hour * 60 + index.minute
    return (mins >= sh * 60 + sm) & (mins < eh * 60 + em)


def stop_for(side: int, mode: str, entry: float, or_high: float, or_low: float) -> float:
    mid = (or_high + or_low) / 2.0
    if mode == "far":
        return or_low if side > 0 else or_high
    if mode == "mid":
        return mid
    if mode == "near":
        return or_high if side > 0 else or_low
    raise ValueError(mode)


def simulate_leg(
    day: pd.DataFrame,
    start_pos: int,
    last_pos: int,
    side: int,
    entry_level: float,
    stop: float,
    rr: float,
    cost_points: float,
    leg: str,
    date_str: str,
) -> tuple[Trade | None, int | None]:
    risk = abs(entry_level - stop)
    if not np.isfinite(risk) or risk <= 0:
        return None, None
    target = entry_level + side * risk * rr

    entry_pos = None
    for pos in range(start_pos, last_pos + 1):
        row = day.iloc[pos]
        hit = row.high >= entry_level if side > 0 else row.low <= entry_level
        if hit:
            entry_pos = pos
            break
    if entry_pos is None:
        return None, None

    exit_price = float(day.iloc[last_pos].close)
    exit_reason = "EOD"
    exit_pos = last_pos

    # 保守處理：同一根M5同時碰停損與停利時，一律先算停損。
    for pos in range(entry_pos, last_pos + 1):
        row = day.iloc[pos]
        stop_hit = row.low <= stop if side > 0 else row.high >= stop
        target_hit = row.high >= target if side > 0 else row.low <= target
        if stop_hit:
            exit_price = stop
            exit_reason = "STOP"
            exit_pos = pos
            break
        if target_hit:
            exit_price = target
            exit_reason = "TP"
            exit_pos = pos
            break

    raw = side * (exit_price - entry_level)
    net = raw - cost_points
    return Trade(
        date=date_str,
        leg=leg,
        side="LONG" if side > 0 else "SHORT",
        entry_time=day.index[entry_pos].isoformat(),
        exit_time=day.index[exit_pos].isoformat(),
        entry=float(entry_level),
        exit=float(exit_price),
        stop=float(stop),
        target=float(target),
        risk=float(risk),
        raw_points=float(raw),
        net_points=float(net),
        r_net=float(net / risk),
        exit_reason=exit_reason,
    ), exit_pos


def get_prior_atr(atr: pd.Series, d) -> float | None:
    values = atr.loc[atr.index < d]
    return None if values.empty else float(values.iloc[-1])


def backtest(intraday: pd.DataFrame, atr: pd.Series, cfg: Config) -> list[Trade]:
    trades: list[Trade] = []
    for d, day in intraday.groupby(intraday.index.date):
        if pd.Timestamp(d).weekday() >= 5:
            continue
        day = day.sort_index()
        daily_atr = get_prior_atr(atr, d)
        if daily_atr is None or daily_atr <= 0:
            continue

        or_bars = day.loc[time_mask(day.index, cfg.or_start, cfg.or_end)]
        if len(or_bars) < 2:
            continue
        or_high = float(or_bars.high.max())
        or_low = float(or_bars.low.min())
        or_size = or_high - or_low
        ratio = or_size / daily_atr
        if not (cfg.min_or_atr <= ratio <= cfg.max_or_atr):
            continue

        a_positions = np.flatnonzero(time_mask(day.index, cfg.or_end, cfg.a_cutoff))
        if len(a_positions) == 0:
            continue
        a_start, a_last = int(a_positions[0]), int(a_positions[-1])
        a_up = or_high + daily_atr * cfg.a_mult
        a_down = or_low - daily_atr * cfg.a_mult

        chosen = None
        for pos in range(a_start, a_last + 1):
            row = day.iloc[pos]
            hit_long = row.high >= a_up
            hit_short = row.low <= a_down
            if hit_long and hit_short:
                chosen = None
                break
            if hit_long:
                chosen = (pos, 1, a_up)
                break
            if hit_short:
                chosen = (pos, -1, a_down)
                break
        if chosen is None:
            continue

        entry_pos, side, level = chosen
        if cfg.trend_filter == "ema200":
            close_now = float(day.iloc[entry_pos].close)
            ema_now = float(day.iloc[entry_pos].ema200)
            if (side > 0 and close_now <= ema_now) or (side < 0 and close_now >= ema_now):
                continue

        eod_positions = np.flatnonzero(time_mask(day.index, cfg.or_end, "16:00"))
        if len(eod_positions) == 0:
            continue
        eod_last = int(eod_positions[-1])
        stop = stop_for(side, cfg.stop_mode, level, or_high, or_low)
        a_trade, a_exit_pos = simulate_leg(
            day, entry_pos, eod_last, side, level, stop, cfg.rr,
            cfg.cost_points, "A", str(d)
        )
        if a_trade is None or a_exit_pos is None:
            continue
        trades.append(a_trade)

        # 原始ACD：只有A單在B點（OR另一側）失敗才啟動C反向交易。
        if not cfg.enable_c or cfg.stop_mode != "far" or a_trade.exit_reason != "STOP":
            continue

        c_side = -side
        c_level = (
            or_low - daily_atr * cfg.c_mult
            if c_side < 0
            else or_high + daily_atr * cfg.c_mult
        )
        c_stop = stop_for(c_side, cfg.stop_mode, c_level, or_high, or_low)
        c_positions = np.flatnonzero(time_mask(day.index, cfg.or_end, cfg.c_cutoff))
        if len(c_positions) == 0:
            continue
        c_last = int(c_positions[-1])
        c_start = max(a_exit_pos + cfg.c_delay_bars, int(c_positions[0]))
        if c_start > c_last:
            continue
        c_trade, _ = simulate_leg(
            day, c_start, c_last, c_side, c_level, c_stop, cfg.rr,
            cfg.cost_points, "C", str(d)
        )
        if c_trade is not None:
            trades.append(c_trade)
    return trades


def metrics(trades: Iterable[Trade]) -> dict[str, float]:
    ts = list(trades)
    if not ts:
        return {
            "trades": 0, "win_rate": np.nan, "profit_factor": np.nan,
            "net_points": 0.0, "avg_r": np.nan, "max_dd_r": np.nan,
            "max_consecutive_losses": 0,
        }
    pnl = np.array([t.net_points for t in ts], dtype=float)
    rs = np.array([t.r_net for t in ts], dtype=float)
    gp = pnl[pnl > 0].sum()
    gl = -pnl[pnl < 0].sum()
    equity = np.cumsum(rs)
    peak = np.maximum.accumulate(np.r_[0.0, equity])
    dd = peak[1:] - equity
    max_losing = 0
    current = 0
    for x in pnl:
        if x < 0:
            current += 1
            max_losing = max(max_losing, current)
        else:
            current = 0
    return {
        "trades": int(len(ts)),
        "win_rate": float((pnl > 0).mean() * 100.0),
        "profit_factor": float(gp / gl) if gl > 0 else np.inf,
        "net_points": float(pnl.sum()),
        "avg_r": float(rs.mean()),
        "max_dd_r": float(dd.max()) if len(dd) else 0.0,
        "max_consecutive_losses": int(max_losing),
    }


def split_trades(trades: list[Trade], cutoff: pd.Timestamp) -> tuple[list[Trade], list[Trade]]:
    train, test = [], []
    for trade in trades:
        (train if pd.Timestamp(trade.date) < cutoff else test).append(trade)
    return train, test


def main() -> None:
    intraday, atr = download_data("GC=F")
    dates = sorted(set(intraday.index.date))
    if len(dates) < 20:
        raise RuntimeError(f"Too few trading dates: {len(dates)}")
    cutoff = pd.Timestamp(dates[int(len(dates) * 0.70)])

    sessions = [
        ("NY0930", "09:30", "09:45", "12:00", "15:00"),
        ("COMEX0820", "08:20", "08:35", "12:00", "15:00"),
    ]
    configs: list[Config] = []
    for sess, a_mult, c_mult, stop_mode, rr, enable_c, trend in itertools.product(
        sessions,
        [0.03, 0.05, 0.08, 0.10, 0.12, 0.15],
        [0.00, 0.03, 0.05],
        ["far", "mid", "near"],
        [1.0, 1.5, 2.0, 2.5, 3.0],
        [False, True],
        ["none", "ema200"],
    ):
        sname, ostart, oend, acut, ccut = sess
        configs.append(Config(
            sname, ostart, oend, acut, ccut, a_mult, c_mult,
            stop_mode, rr, enable_c, trend
        ))

    rows = []
    default_trades: list[Trade] = []
    default_cfg = Config(
        "NY0930", "09:30", "09:45", "12:00", "15:00",
        0.10, 0.03, "far", 2.0, True, "none"
    )

    for i, cfg in enumerate(configs, 1):
        trades = backtest(intraday, atr, cfg)
        train, test = split_trades(trades, cutoff)
        row = asdict(cfg)
        row.update({f"all_{k}": v for k, v in metrics(trades).items()})
        row.update({f"train_{k}": v for k, v in metrics(train).items()})
        row.update({f"test_{k}": v for k, v in metrics(test).items()})
        rows.append(row)
        if cfg == default_cfg:
            default_trades = trades
        if i % 500 == 0:
            print(f"processed {i}/{len(configs)}")

    results = pd.DataFrame(rows)
    results.to_csv(OUT / "acd_grid_results.csv", index=False)
    pd.DataFrame([asdict(t) for t in default_trades]).to_csv(
        OUT / "acd_default_trades.csv", index=False
    )

    eligible = results[
        (results.train_trades >= 15) & np.isfinite(results.train_profit_factor)
    ].copy()
    eligible["train_score"] = (
        eligible.train_avg_r.clip(lower=-1, upper=2)
        * np.sqrt(eligible.train_trades)
        - 0.02 * eligible.train_max_dd_r
    )
    top = eligible.sort_values(
        ["train_score", "train_profit_factor"], ascending=False
    ).head(25)
    top.to_csv(OUT / "acd_top25_discovery_ranked.csv", index=False)

    default_train, default_test = split_trades(default_trades, cutoff)
    lines = [
        "# ACD Gold Preliminary Backtest",
        "",
        "- Instrument: GC=F (Yahoo Finance 5-minute data)",
        f"- Data: {intraday.index.min()} to {intraday.index.max()}",
        f"- Trading dates: {len(dates)}",
        f"- Discovery/validation cutoff: {cutoff.date()} (70/30 chronological split)",
        "- Round-trip cost: 0.25 gold points per trade.",
        "- Conservative rules: dual breakout bar skipped; stop wins when stop and target touch the same M5 bar.",
        "",
        "## Default configuration",
        "",
        "NY 09:30–09:45 OR; A=0.10 ATR; C=0.03 ATR; far-side stop; 2R; C enabled; no trend filter.",
        "",
        f"- All: {json.dumps(metrics(default_trades), ensure_ascii=False)}",
        f"- Discovery: {json.dumps(metrics(default_train), ensure_ascii=False)}",
        f"- Validation: {json.dumps(metrics(default_test), ensure_ascii=False)}",
        "",
        "## Top 10 selected only by discovery score",
        "",
    ]
    cols = [
        "session_name", "a_mult", "c_mult", "stop_mode", "rr",
        "enable_c", "trend_filter", "train_trades", "train_win_rate",
        "train_profit_factor", "train_avg_r", "train_max_dd_r",
        "test_trades", "test_win_rate", "test_profit_factor",
        "test_avg_r", "test_max_dd_r",
    ]
    lines.append(top.head(10)[cols].to_markdown(index=False, floatfmt=".3f"))
    lines += [
        "",
        "## Limits",
        "",
        "This is a preliminary 60-day screen, not proof of long-term profitability. Only broad parameter neighborhoods that remain positive in validation should advance to multi-year broker/tick testing.",
    ]
    (OUT / "acd_summary.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "data_info.json").write_text(
        json.dumps({
            "intraday_start": intraday.index.min().isoformat(),
            "intraday_end": intraday.index.max().isoformat(),
            "bars": int(len(intraday)),
            "trading_dates": len(dates),
            "cutoff": cutoff.date().isoformat(),
            "configs": len(configs),
        }, indent=2),
        encoding="utf-8",
    )
    print((OUT / "acd_summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
