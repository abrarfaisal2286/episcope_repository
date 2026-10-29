"""Builds report figures from the pipeline outputs (matplotlib). Run after `make all`."""
import json
import pathlib
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np
import pandas as pd
from common import config

FIG = pathlib.Path(__file__).resolve().parent / "figures"
FIG.mkdir(exist_ok=True)
plt.rcParams.update({"font.size": 8, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 200})
CB = {"c2": "#0072B2", "fl": "#CC79A7", "cusum": "#009E73", "c2_naive": "#D55E00"}


def architecture():
    fig, ax = plt.subplots(figsize=(7.2, 3.2)); ax.axis("off"); ax.set_xlim(0, 100); ax.set_ylim(0, 46)
    def box(x, y, w, h, t, fc="#ffffff", fs=5.8):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.3,rounding_size=1", fc=fc, ec="black", lw=0.8))
        ax.text(x + w / 2, y + h / 2, t, ha="center", va="center", fontsize=fs)
    def arrow(x1, y1, x2, y2):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="->", lw=0.8))
    for i, t in enumerate(["S1 boundaries\nS2 population", "S3 weather\n(API + cache)", "S4 encounters\n(CSV, daily)", "S5 registry\nS6 stock, beds"]):
        box(1, 35 - i * 10, 15, 8, t, "#eeeeee"); arrow(16.5, 39 - i * 10, 21, 24)
    box(21, 15, 14, 18, "Ingestion\nchecksum\nledger\nbatch and\nstreaming")
    box(38, 15, 13, 18, "Bronze\nraw, immutable\nmetadata", "#f3e5d0")
    box(54, 15, 17, 18, "Silver\n17 rules,\nquarantine,\nsalted hash,\nage bands,\npatient de-dup", "#e0e0e0")
    box(74, 15, 12, 18, "Gold\nepi-week\nmarts, report\ncompleteness,\nquality report", "#fff2b3", 5.4)
    for a, b in ((35.6, 37.6), (51.6, 53.6), (71.6, 73.6)):
        arrow(a, 24, b, 24)
    box(54, 1.5, 32, 8.5, "Analytics: detection (C2, seasonal, CUSUM),\nNB forecasts, demand, LP allocation, calendar", "#d6ebf5")
    arrow(80, 14.5, 74, 10.5)
    box(89, 15, 10, 18, "Serving\nsuppressed\nJSON\n(under\n10 MB)", "#e5f2e5"); arrow(86.6, 24, 88.6, 24)
    box(89, 36, 10, 8, "D3.js\ndashboard", "#e5f2e5"); arrow(94, 33.6, 94, 35.6)
    box(21, 1.5, 30, 8.5, "Orchestration (retrying DAG), quality checks,\nquarantine, lineage, tests", "#f5f5f5")
    fig.savefig(FIG / "architecture.png", bbox_inches="tight"); plt.close(fig)


def detection(tag="history"):
    p = config.OUTPUTS / f"detection_tradeoff_{tag}.csv"
    if not p.exists():
        return
    c = pd.read_csv(p); op = json.load(open(config.OUTPUTS / f"operating_point_{tag}.json"))
    fig, ax = plt.subplots(figsize=(3.4, 2.6))
    lab = {"c2": "EARS C2", "fl": "Seasonal (Farrington-style)", "cusum": "CUSUM on seasonal residuals", "c2_naive": "EARS C2, no artefact guard"}
    for m in ("c2", "fl", "cusum", "c2_naive"):
        d = c[c["method"] == m].sort_values("threshold")
        ax.plot(d["false_per_district_year"], d["sensitivity"], marker="o", ms=2.5, lw=1, color=CB[m], label=lab[m], ls="--" if m == "c2_naive" else "-")
    ax.axvline(1.0, color="grey", lw=0.6, ls=":"); ax.text(1.05, 0.05, "1 per\ndistrict-year", fontsize=6, color="grey")
    ax.plot(op["metrics"]["false_per_district_year"], op["metrics"]["sensitivity"], "k*", ms=9, label="chosen operating point")
    ax.set_xlabel("False alert episodes per district-year"); ax.set_ylabel("Sensitivity"); ax.set_ylim(0, 1.02)
    ax.legend(fontsize=5.5, loc="lower right"); fig.savefig(FIG / "detection_tradeoff.png", bbox_inches="tight"); plt.close(fig)


def forecast():
    p = config.OUTPUTS / "forecast_backtest_by_horizon.csv"
    if not p.exists():
        return
    d = pd.read_csv(p)
    fig, axs = plt.subplots(1, 3, figsize=(7.2, 2.3))
    lab = {"nb_weather": "NB GLM with weather", "nb_noweather": "NB GLM without weather", "seasonal_naive": "Seasonal naive"}
    col = {"nb_weather": "#0072B2", "nb_noweather": "#56B4E9", "seasonal_naive": "#999999"}
    for dis, ls in (("dengue", "-"), ("diarrhoea", "--"), ("malaria", ":")):
        for m in lab:
            x = d[(d["disease_group"] == dis) & (d["model"] == m)].sort_values("horizon")
            if x.empty:
                continue
            axs[0].plot(x["horizon"], x["mae"] / x["mean_observed"], ls, color=col[m], marker="o", ms=2, label=f"{dis}, {lab[m]}" if dis == "dengue" else None)
            axs[1].plot(x["horizon"], x["coverage80"], ls, color=col[m], marker="o", ms=2)
            axs[2].plot(x["horizon"], x["wis"] / x["mean_observed"], ls, color=col[m], marker="o", ms=2)
    axs[0].set_ylabel("MAE / mean cases"); axs[1].set_ylabel("80% interval coverage"); axs[1].axhline(0.8, color="k", lw=0.5, ls=":")
    axs[2].set_ylabel("WIS / mean cases")
    for a in axs:
        a.set_xlabel("Horizon (weeks)"); a.set_xticks([1, 2, 3, 4])
    axs[0].legend(fontsize=5.5); fig.text(0.5, -0.04, "solid: dengue, dashed: diarrhoea, dotted: malaria", ha="center", fontsize=6)
    fig.tight_layout(); fig.savefig(FIG / "forecast_backtest.png", bbox_inches="tight"); plt.close(fig)


def equity():
    p = config.OUTPUTS / "allocation_scenarios.csv"
    if not p.exists():
        return
    s = pd.read_csv(p)
    fig, axs = plt.subplots(1, 2, figsize=(7.2, 2.5))
    for bf, g in s.groupby("budget_fraction"):
        g = g.sort_values("alpha")
        axs[0].plot(g["gini_coverage"], g["weighted_unmet"] / 1e3, "-o", ms=2.5, lw=1, label=f"budget {int(bf*100)}%")
        axs[1].plot(g["alpha"], g["efficiency_loss_vs_alpha0"] * 100, "-o", ms=2.5, lw=1)
    axs[0].set_xlabel("Inequality of coverage across districts (Gini)"); axs[0].set_ylabel("Weighted unmet demand (thousand USD)")
    axs[0].legend(fontsize=5.5); axs[1].set_xlabel("Equity floor alpha"); axs[1].set_ylabel("Efficiency loss vs alpha = 0 (%)")
    fig.tight_layout(); fig.savefig(FIG / "efficiency_equity.png", bbox_inches="tight"); plt.close(fig)


def quality():
    p = config.OUTPUTS / "dq_evaluation.json"
    if not p.exists():
        return
    j = json.load(open(p)); r = pd.DataFrame(j["rules"]); r = r[r["fail"] > 0].sort_values("fail_rate")
    fig, ax = plt.subplots(figsize=(3.4, 2.6)); ax.barh(r["rule_code"].str.replace("_", " ", n=1), r["fail_rate"] * 100, color="#0072B2")
    ax.set_xlabel("Share of received encounter records failing the rule (%)"); ax.tick_params(axis="y", labelsize=5.5)
    fig.savefig(FIG / "quality_rules.png", bbox_inches="tight"); plt.close(fig)


def scaling():
    p = pathlib.Path(__file__).resolve().parent / "scalability_results.csv"
    if not p.exists():
        return
    d = pd.read_csv(p); cols = [c for c in d.columns if c.startswith("t_")]
    fig, ax = plt.subplots(figsize=(3.4, 2.6)); bottom = np.zeros(len(d))
    for c in cols:
        ax.bar(d["sf"].astype(str), d[c] / 60, bottom=bottom, label=c[2:]); bottom += (d[c] / 60).to_numpy()
    ax.set_xlabel("Scale factor"); ax.set_ylabel("Minutes"); ax.legend(fontsize=5, ncol=2); fig.savefig(FIG / "scaling.png", bbox_inches="tight"); plt.close(fig)


if __name__ == "__main__":
    architecture(); detection(); forecast(); equity(); quality(); scaling()
    print(sorted(x.name for x in FIG.glob("*.png")))
