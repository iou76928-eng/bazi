from __future__ import annotations

import itertools
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from acd_backtest import Config, backtest, metrics
from acd_backtest_multiyear import load_xauusd

OUT = Path("artifacts_optimizer")
OUT.mkdir(exist_ok=True)

BASE = dict(
    session_name="NY0930",
    or_start="09:30",
    or_end="09:45",
    c_cutoff="15:00",
    a_mult=0.05,
    c_mult=0.03,
    stop_mode="far",
    rr=1.0,
    trend_filter="ema200",
    cost_points=0.25,
)

WEEKDAY_SETS = {
    "ALL": {0, 1, 2, 3, 4},
    "NO_MON": {1, 2, 3, 4},
    "NO_FRI": {0, 1, 2, 3},
    "MON_THU": {0, 1, 2, 3},
    "TUE_FRI": {1, 2, 3, 4},
    "TUE_THU": {1, 2, 3},
    "MON_WED_FRI": {0, 2, 4},
}

OR_RANGES = [
    (0.03, 0.15),
    (0.03, 0.20),
    (0.03, 0.25),
    (0.03, 0.35),
    (0.05, 0.20),
    (0.05, 0.25),
    (0.05, 0.35),
    (0.08, 0.20),
    (0.08, 0.25),
    (0.08, 0.35),
]


def filter_trades(trades, direction: str, weekdays: set[int]):
    result = []
    for t in trades:
        if direction == "LONG" and t.side != "LONG":
            continue
        if direction == "SHORT" and t.side != "SHORT":
            continue
        if pd.Timestamp(t.date).weekday() not in weekdays:
            continue
        result.append(t)
    return result


def period_metrics(trades, start_year: int | None = None, end_year: int | None = None):
    selected = []
    for t in trades:
        year = int(t.date[:4])
        if start_year is not None and year < start_year:
            continue
        if end_year is not None and year > end_year:
            continue
        selected.append(t)
    return metrics(selected)


def attribution_table(trades, field: str) -> pd.DataFrame:
    rows = []
    values = sorted({getattr(t, field) for t in trades})
    for value in values:
        subset = [t for t in trades if getattr(t, field) == value]
        row = {field: value}
        row.update(metrics(subset))
        rows.append(row)
    return pd.DataFrame(rows)


def weekday_table(trades) -> pd.DataFrame:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    rows = []
    for dow in range(5):
        subset = [t for t in trades if pd.Timestamp(t.date).weekday() == dow]
        row = {"weekday": names[dow]}
        row.update(metrics(subset))
        rows.append(row)
    return pd.DataFrame(rows)


def entry_bucket_table(trades) -> pd.DataFrame:
    rows = []
    buckets = {}
    for t in trades:
        ts = pd.Timestamp(t.entry_time)
        minute = ts.hour * 60 + ts.minute
        bucket_start = minute - minute % 30
        label = f"{bucket_start // 60:02d}:{bucket_start % 60:02d}"
        buckets.setdefault(label, []).append(t)
    for label, subset in sorted(buckets.items()):
        row = {"entry_bucket": label}
        row.update(metrics(subset))
        rows.append(row)
    return pd.DataFrame(rows)


def build_config(cutoff: str, enable_c: bool, min_or: float, max_or: float, cost: float = 0.25):
    return Config(
        BASE["session_name"],
        BASE["or_start"],
        BASE["or_end"],
        cutoff,
        BASE["c_cutoff"],
        BASE["a_mult"],
        BASE["c_mult"],
        BASE["stop_mode"],
        BASE["rr"],
        enable_c,
        BASE["trend_filter"],
        cost_points=cost,
        min_or_atr=min_or,
        max_or_atr=max_or,
    )


def main():
    intraday, atr = load_xauusd()

    baseline_cfg = build_config("12:00", False, 0.03, 0.35)
    baseline_trades = backtest(intraday, atr, baseline_cfg)

    attribution_table(baseline_trades, "leg").to_csv(OUT / "baseline_by_leg.csv", index=False)
    attribution_table(baseline_trades, "side").to_csv(OUT / "baseline_by_side.csv", index=False)
    weekday_table(baseline_trades).to_csv(OUT / "baseline_by_weekday.csv", index=False)
    entry_bucket_table(baseline_trades).to_csv(OUT / "baseline_by_entry_bucket.csv", index=False)

    rows = []
    trade_cache = {}
    configs = list(itertools.product(
        ["10:00", "10:30", "11:00", "12:00"],
        [False, True],
        OR_RANGES,
    ))

    for i, (cutoff, enable_c, (min_or, max_or)) in enumerate(configs, 1):
        cfg = build_config(cutoff, enable_c, min_or, max_or)
        raw_trades = backtest(intraday, atr, cfg)
        trade_cache[(cutoff, enable_c, min_or, max_or, 0.25)] = raw_trades

        for direction, weekday_name in itertools.product(
            ["BOTH", "LONG", "SHORT"], WEEKDAY_SETS
        ):
            filtered = filter_trades(raw_trades, direction, WEEKDAY_SETS[weekday_name])
            row = {
                "cutoff": cutoff,
                "enable_c": enable_c,
                "min_or_atr": min_or,
                "max_or_atr": max_or,
                "direction": direction,
                "weekdays": weekday_name,
            }
            row.update({f"disc_{k}": v for k, v in period_metrics(filtered, None, 2021).items()})
            row.update({f"y2022_{k}": v for k, v in period_metrics(filtered, 2022, 2022).items()})
            row.update({f"y2023_{k}": v for k, v in period_metrics(filtered, 2023, 2023).items()})
            row.update({f"all_{k}": v for k, v in metrics(filtered).items()})
            rows.append(row)

        if i % 20 == 0:
            print(f"base configs {i}/{len(configs)}")

    results = pd.DataFrame(rows)
    results["disc_score"] = (
        results.disc_avg_r.clip(lower=-1, upper=2) * np.sqrt(results.disc_trades.clip(lower=0))
        - 0.025 * results.disc_max_dd_r
    )
    results.to_csv(OUT / "filter_grid.csv", index=False)

    ranked = results[
        (results.disc_trades >= 80)
        & np.isfinite(results.disc_profit_factor)
        & (results.disc_profit_factor > 1.0)
        & (results.disc_avg_r > 0.0)
    ].sort_values(["disc_score", "disc_profit_factor"], ascending=False)
    ranked.head(50).to_csv(OUT / "top50_discovery.csv", index=False)

    robust = ranked[
        (ranked.y2022_trades >= 20)
        & (ranked.y2023_trades >= 15)
        & (ranked.y2022_profit_factor > 1.0)
        & (ranked.y2023_profit_factor > 1.0)
        & (ranked.y2022_avg_r > 0.0)
        & (ranked.y2023_avg_r > 0.0)
    ].copy()
    robust.to_csv(OUT / "validation_positive.csv", index=False)

    strict = robust[
        (robust.all_profit_factor >= 1.20)
        & (robust.all_avg_r >= 0.08)
        & (robust.all_max_dd_r <= 10.0)
    ].copy()
    strict.to_csv(OUT / "strict_pass.csv", index=False)

    stress_rows = []
    for rank, rec in ranked.head(20).reset_index(drop=True).iterrows():
        for cost in [0.25, 0.50, 1.00]:
            key = (rec.cutoff, bool(rec.enable_c), rec.min_or_atr, rec.max_or_atr, cost)
            if key not in trade_cache:
                cfg = build_config(rec.cutoff, bool(rec.enable_c), rec.min_or_atr, rec.max_or_atr, cost)
                trade_cache[key] = backtest(intraday, atr, cfg)
            filtered = filter_trades(
                trade_cache[key], rec.direction, WEEKDAY_SETS[rec.weekdays]
            )
            row = {
                "discovery_rank": rank + 1,
                "cost_points": cost,
                "cutoff": rec.cutoff,
                "enable_c": bool(rec.enable_c),
                "min_or_atr": rec.min_or_atr,
                "max_or_atr": rec.max_or_atr,
                "direction": rec.direction,
                "weekdays": rec.weekdays,
            }
            row.update({f"disc_{k}": v for k, v in period_metrics(filtered, None, 2021).items()})
            row.update({f"y2022_{k}": v for k, v in period_metrics(filtered, 2022, 2022).items()})
            row.update({f"y2023_{k}": v for k, v in period_metrics(filtered, 2023, 2023).items()})
            row.update({f"all_{k}": v for k, v in metrics(filtered).items()})
            stress_rows.append(row)
    stress = pd.DataFrame(stress_rows)
    stress.to_csv(OUT / "cost_stress_top20.csv", index=False)

    stable_cost = stress.groupby("discovery_rank").filter(
        lambda g: (
            (g.loc[g.cost_points == 0.50, "all_profit_factor"] > 1.0).all()
            and (g.loc[g.cost_points == 1.00, "all_profit_factor"] > 1.0).all()
        )
    )
    stable_ranks = sorted(stable_cost.discovery_rank.unique().tolist()) if not stable_cost.empty else []

    display_cols = [
        "cutoff", "enable_c", "min_or_atr", "max_or_atr", "direction", "weekdays",
        "disc_trades", "disc_profit_factor", "disc_avg_r", "disc_max_dd_r",
        "y2022_trades", "y2022_profit_factor", "y2022_avg_r",
        "y2023_trades", "y2023_profit_factor", "y2023_avg_r",
        "all_trades", "all_profit_factor", "all_avg_r", "all_max_dd_r",
    ]

    by_leg = pd.read_csv(OUT / "baseline_by_leg.csv")
    by_side = pd.read_csv(OUT / "baseline_by_side.csv")
    by_weekday = pd.read_csv(OUT / "baseline_by_weekday.csv")
    by_entry = pd.read_csv(OUT / "baseline_by_entry_bucket.csv")

    lines = [
        "# ACD Filter Optimization",
        "",
        f"Data: {intraday.index.min()} to {intraday.index.max()}, {len(intraday):,} M5 bars.",
        "Base: NY 09:30–09:45; A=0.05 ATR; C=0.03 ATR; EMA200; far-side stop; 1R; cost=0.25.",
        "Selection uses data through 2021 only; 2022 and 2023 are untouched validation years.",
        "",
        "## Baseline attribution",
        "",
        "### By leg",
        "",
        by_leg.to_markdown(index=False, floatfmt=".3f"),
        "",
        "### By side",
        "",
        by_side.to_markdown(index=False, floatfmt=".3f"),
        "",
        "### By weekday",
        "",
        by_weekday.to_markdown(index=False, floatfmt=".3f"),
        "",
        "### By entry time bucket",
        "",
        by_entry.to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Top 15 selected only on discovery data",
        "",
        ranked.head(15)[display_cols].to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Positive in both 2022 and 2023",
        "",
        f"Count: {len(robust)}",
        "",
        robust.head(30)[display_cols].to_markdown(index=False, floatfmt=".3f") if len(robust) else "None.",
        "",
        "## Strict target passed",
        "",
        "Rules: all PF>=1.20, avg>=0.08R, max DD<=10R, plus both validation years positive.",
        f"Count: {len(strict)}",
        "",
        strict.head(30)[display_cols].to_markdown(index=False, floatfmt=".3f") if len(strict) else "None.",
        "",
        "## Cost stress",
        "",
        f"Top-20 discovery ranks still PF>1 at both 0.50 and 1.00 cost: {stable_ranks}",
    ]
    (OUT / "optimization_report.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "optimization_info.json").write_text(json.dumps({
        "rows": len(results),
        "ranked": len(ranked),
        "validation_positive": len(robust),
        "strict_pass": len(strict),
        "cost_stable_ranks": stable_ranks,
    }, indent=2), encoding="utf-8")
    print((OUT / "optimization_report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
