from __future__ import annotations

from pathlib import Path
import pandas as pd

from acd_backtest import Config, backtest, metrics
from acd_backtest_multiyear import load_xauusd
from acd_optimize_filters import filter_trades, period_metrics

OUT = Path("artifacts_shortlist")
OUT.mkdir(exist_ok=True)

SHORTLIST = [
    dict(name="LONG_1200_OR05_35_noC", cutoff="12:00", enable_c=False, min_or=0.05, max_or=0.35, direction="LONG", weekdays={0,1,2,3,4}),
    dict(name="LONG_1200_OR05_35_noMon_noC", cutoff="12:00", enable_c=False, min_or=0.05, max_or=0.35, direction="LONG", weekdays={1,2,3,4}),
    dict(name="LONG_1200_OR05_35_TueThu_noC", cutoff="12:00", enable_c=False, min_or=0.05, max_or=0.35, direction="LONG", weekdays={1,2,3}),
    dict(name="LONG_1100_OR05_35_noC", cutoff="11:00", enable_c=False, min_or=0.05, max_or=0.35, direction="LONG", weekdays={0,1,2,3,4}),
    dict(name="BOTH_1200_OR08_35_noFri_noC", cutoff="12:00", enable_c=False, min_or=0.08, max_or=0.35, direction="BOTH", weekdays={0,1,2,3}),
    dict(name="LONG_1200_OR05_25_withC", cutoff="12:00", enable_c=True, min_or=0.05, max_or=0.25, direction="LONG", weekdays={0,1,2,3,4}),
]


def make_cfg(item, cost):
    return Config(
        "NY0930", "09:30", "09:45", item["cutoff"], "15:00",
        0.05, 0.03, "far", 1.0, item["enable_c"], "ema200",
        cost_points=cost, min_or_atr=item["min_or"], max_or_atr=item["max_or"]
    )


def main():
    intraday, atr = load_xauusd()
    rows = []
    for item in SHORTLIST:
        for cost in [0.25, 0.50, 0.75, 1.00]:
            trades = backtest(intraday, atr, make_cfg(item, cost))
            trades = filter_trades(trades, item["direction"], item["weekdays"])
            row = {"name": item["name"], "cost_points": cost}
            row.update({f"disc_{k}": v for k, v in period_metrics(trades, None, 2021).items()})
            row.update({f"y2022_{k}": v for k, v in period_metrics(trades, 2022, 2022).items()})
            row.update({f"y2023_{k}": v for k, v in period_metrics(trades, 2023, 2023).items()})
            row.update({f"all_{k}": v for k, v in metrics(trades).items()})
            rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "shortlist_cost_stress.csv", index=False)
    cols = [
        "name", "cost_points", "all_trades", "all_profit_factor", "all_avg_r", "all_max_dd_r",
        "y2022_profit_factor", "y2022_avg_r", "y2023_profit_factor", "y2023_avg_r"
    ]
    lines = [
        "# ACD Shortlist Cost Stress",
        "",
        "All configurations were selected before this cost stress.",
        "",
        df[cols].to_markdown(index=False, floatfmt=".3f"),
    ]
    (OUT / "shortlist_cost_stress.md").write_text("\n".join(lines), encoding="utf-8")
    print((OUT / "shortlist_cost_stress.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
