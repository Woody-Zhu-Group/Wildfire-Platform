"""Static figures for FINDINGS.md.

Run from the repository root after ``ingest`` (needs matplotlib, which is in
requirements-analysis.txt)::

    PYTHONPATH=. python -m analysis.sectionalization.figures

Colors follow the workspace's light theme: method identity uses the same
validated categorical slots as ``website/src/index.css`` (--method-N), and
magnitude uses one blue ramp.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, PowerNorm  # noqa: E402

from analysis.sectionalization.analyze import FACTORIAL, PROPOSED  # noqa: E402
from analysis.sectionalization.model import METHODS  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "figures"
METHOD_COLOR = {
    "group_conformal_random": "#2a78d6", "co_optimized": "#eb6834", "planning_only": "#1baf7a",
    "group_conformal_fixed": "#eda100", "group_conformal_oracle": "#e87ba4", "bonferroni": "#008300",
    "maxrank": "#4a3aa7", "ci": "#e34948",
}
HEADLINE = ("group_conformal_random", "co_optimized", "planning_only")
INK, SECONDARY, MUTED, GRID, CONTEXT = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
FAST_TRIP, PSPS, TRACK = "#eb6834", "#1baf7a", "#eceae4"
LABEL = {m.slug: m.label for m in METHODS}


def style() -> None:
    plt.rcParams.update({
        "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
        "axes.edgecolor": CONTEXT, "axes.labelcolor": SECONDARY, "axes.titlecolor": INK,
        "axes.titlesize": 11, "axes.titleweight": "bold", "axes.labelsize": 9,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelsize": 8.5, "ytick.labelsize": 8.5,
        "text.color": INK, "font.size": 9, "legend.frameon": False, "legend.fontsize": 8.5,
        "axes.spines.top": False, "axes.spines.right": False,
    })


def _grid(ax, axis: str = "x") -> None:
    ax.grid(axis=axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def load() -> pd.DataFrame:
    runs = pd.read_csv(HERE / "data" / "runs.csv")
    runs = runs[runs["complete_setup"]].copy()
    runs["cap_share_fast_trip"] = runs["saifi_fast_trip"] / runs["W"]
    runs["cap_share_psps_realized"] = runs["saifi_psps_realized"] / runs["W"]
    ranking = pd.read_csv(HERE / "data" / "ranking_baselines.csv")[["setup_id", "exp", "rank_exposure_exposure"]]
    runs = runs.merge(ranking, on=["setup_id", "exp"], how="left")
    runs["risk_reduction_rank"] = 1.0 - runs["rank_exposure_exposure"] / runs["exposure_baseline"]
    return runs


RANK_LABEL = "Rank by predicted exposure (not in the grid)"


def _end_labels(ax, items: list[tuple[float, float, str]], min_gap: float) -> None:
    """Label line ends; a label too close to one already placed is left to the legend."""

    placed: list[float] = []
    for x, y, text in sorted(items, key=lambda item: -item[1]):
        if all(abs(y - other) >= min_gap for other in placed):
            ax.annotate(text, (x, y), xytext=(6, 0), textcoords="offset points", va="center", fontsize=8.5, color=SECONDARY)
            placed.append(y)


def methods_figure(runs: pd.DataFrame) -> None:
    order = [m.slug for m in METHODS]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4), sharey=True, sharex=True)
    for ax, column, title in ((axes[0], "risk_reduction", "As solved"), (axes[1], "risk_reduction_filled", "Unused sectionalization budget filled")):
        stats = runs.groupby("method")[column].describe(percentiles=[0.1, 0.5, 0.9]).reindex(order)
        y = np.arange(len(order))[::-1]
        rank = runs.drop_duplicates(["setup_id", "exp"])["risk_reduction_rank"].median() * 100
        ax.axvline(rank, color=INK, linewidth=1.2, linestyle=(0, (4, 3)), zorder=4, label=f"{RANK_LABEL}, median {rank:.0f}%")
        ax.barh(y, stats["50%"] * 100, height=0.5, color="#2a78d6")
        ax.hlines(y, stats["10%"] * 100, stats["90%"] * 100, color=INK, linewidth=1.2, alpha=0.55)
        for yi, median in zip(y, stats["50%"]):
            # A value column right of the plot, so no line or bar ever crosses a value.
            ax.text(82, yi, f"{median * 100:.0f}%", va="center", ha="left", fontsize=8.5, color=SECONDARY, clip_on=False)
        ax.set_title(title, loc="left")
        ax.set_xlabel("Risk reduction (share of unmitigated exposure removed, %)")
        ax.set_xlim(0, 80)
        ax.set_ylim(-0.6, len(order) - 0.1)
        _grid(ax)
    axes[0].set_yticks(np.arange(len(order))[::-1], [LABEL[s] for s in order])
    fig.suptitle("Bars: median over 137 setups x 5 replications. Lines: 10th to 90th percentile.", x=0.01, ha="left", fontsize=8.5, color=SECONDARY)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=1, bbox_to_anchor=(0.55, 0.0))
    fig.tight_layout(rect=(0, 0.06, 1, 0.96))
    fig.savefig(OUT / "methods_solved_vs_filled.png", dpi=150)
    plt.close(fig)


def saifi_cap_figure(runs: pd.DataFrame) -> None:
    mask = np.ones(len(runs), dtype=bool)
    for key, levels in FACTORIAL.items():
        mask &= runs[key].round(4).isin(levels)
    block = runs[mask]
    fig, axes = plt.subplots(1, 2, figsize=(10, 5.0), sharey=True)
    levels = FACTORIAL["W"]
    for ax, column, title in ((axes[0], "risk_reduction", "As solved"), (axes[1], "risk_reduction_filled", "Unused sectionalization budget filled")):
        means = block.groupby(["method", "W"])[column].mean().unstack("W")
        for slug in [m.slug for m in METHODS if m.slug not in HEADLINE]:
            ax.plot(levels, means.loc[slug] * 100, color=CONTEXT, linewidth=1.5, zorder=1)
        ends = []
        for slug in HEADLINE:
            values = means.loc[slug] * 100
            ax.plot(levels, values, color=METHOD_COLOR[slug], linewidth=2, marker="o", markersize=6,
                    markeredgecolor="white", markeredgewidth=1.5, label=LABEL[slug], zorder=3)
            ends.append((levels[-1], values.iloc[-1], f"{values.iloc[-1]:.0f}%"))
        rank = block.drop_duplicates(["setup_id", "exp"]).groupby("W")["risk_reduction_rank"].mean() * 100
        ax.plot(levels, rank.reindex(levels), color=INK, linewidth=1.5, linestyle=(0, (4, 3)), label=RANK_LABEL, zorder=2)
        ends.append((levels[-1], rank.iloc[-1], f"{rank.iloc[-1]:.0f}%"))
        _end_labels(ax, ends, min_gap=2.5)
        ax.plot([], [], color=CONTEXT, linewidth=1.5, label="Other five methods")
        ax.set_title(title, loc="left")
        ax.set_xticks(levels, [f"{w:.1f}" for w in levels])
        ax.set_xlim(0.085, 0.33)
        ax.set_xlabel("SAIFI cap (mitigation interruptions per customer-year)")
        _grid(ax, "y")
    axes[0].set_ylabel("Mean risk reduction (%)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle("Mean over the balanced 108-setup block (C 30 or 40%, B 20 or 30%, E 70 to 90%, G 0.80 to 0.95) and 5 replications.",
                 x=0.01, ha="left", fontsize=8.5, color=SECONDARY)
    fig.tight_layout(rect=(0, 0.1, 1, 0.95))
    fig.savefig(OUT / "risk_reduction_by_saifi_cap.png", dpi=150)
    plt.close(fig)


def allocation_figure(runs: pd.DataFrame) -> None:
    order = [m.slug for m in METHODS]
    shares = runs.groupby("method")[["cap_share_fast_trip", "cap_share_psps_realized"]].mean().reindex(order)
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    y = np.arange(len(order))[::-1]
    ax.barh(y, np.ones(len(order)) * 100, height=0.55, color=TRACK, label="Unused")
    ax.barh(y, shares["cap_share_fast_trip"] * 100, height=0.55, color=FAST_TRIP, edgecolor="white", linewidth=2, label="Fast-trip settings")
    ax.barh(y, shares["cap_share_psps_realized"] * 100, left=shares["cap_share_fast_trip"] * 100, height=0.55, color=PSPS,
            edgecolor="white", linewidth=2, label="PSPS in the test year")
    for yi, (ft, ps) in zip(y, shares.to_numpy()):
        ax.text(101.5, yi, f"{(ft + ps) * 100:.0f}% used", va="center", fontsize=8.5, color=SECONDARY)
    ax.set_yticks(y, [LABEL[s] for s in order])
    ax.set_xlim(0, 112)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("Share of the SAIFI cap (%), mean over all setups and replications")
    ax.set_title("How each plan spends the reliability budget", loc="left")
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles[1:] + handles[:1], labels[1:] + labels[:1], loc="lower center", bbox_to_anchor=(0.45, -0.32), ncol=3)
    fig.tight_layout()
    fig.savefig(OUT / "saifi_cap_allocation.png", dpi=150)
    plt.close(fig)


def fast_trip_figure(runs: pd.DataFrame) -> None:
    slugs = (PROPOSED, "co_optimized", "bonferroni")
    e_levels = sorted(runs["E"].round(2).unique())
    g_levels = sorted(runs["G"].round(2).unique())
    ramp = LinearSegmentedColormap.from_list("blue", ["#f4f8fd", "#86b6ef", "#2a78d6", "#104281"])
    tables = {s: runs[runs["method"] == s].assign(E=lambda d: d["E"].round(2), G=lambda d: d["G"].round(2))
              .pivot_table(index="E", columns="G", values="fast_trip", aggfunc="mean").reindex(index=e_levels, columns=g_levels)
              for s in slugs}
    top = max(float(t.max().max()) for t in tables.values())
    norm = PowerNorm(gamma=0.4, vmin=0, vmax=top)
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.6), sharey=True)
    for ax, slug in zip(axes, slugs):
        table = tables[slug]
        ax.imshow(table.to_numpy(), cmap=ramp, norm=norm, aspect="auto", origin="lower")
        for i, e in enumerate(e_levels):
            for j, g in enumerate(g_levels):
                value = table.loc[e, g]
                ratio = e / g
                shade = norm(value) if np.isfinite(value) else 0
                ax.text(j, i, f"{value:.1f}" if value < 10 else f"{value:.0f}", ha="center", va="center", fontsize=9,
                        color="white" if shade > 0.55 else INK, fontweight="bold" if ratio > 1 else "normal")
                ax.text(j, i - 0.32, f"E/G {ratio:.2f}", ha="center", va="center", fontsize=6.5,
                        color="white" if shade > 0.55 else MUTED)
        ax.set_title(LABEL[slug], loc="left", fontsize=10)
        ax.set_xticks(range(len(g_levels)), [f"{g:.2f}" for g in g_levels])
        ax.set_xlabel("Fast-trip SAIFI factor G")
        for spine in ax.spines.values():
            spine.set_visible(False)
    axes[0].set_yticks(range(len(e_levels)), [f"{e:.0%}" for e in e_levels])
    axes[0].set_ylabel("Fast-trip effectiveness E")
    fig.suptitle("Mean fast-trip circuits per plan. Fast-trip is chosen at scale only where E/G > 1 (bold).", x=0.01, ha="left",
                 fontsize=9, color=SECONDARY)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(OUT / "fast_trip_threshold.png", dpi=150)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    style()
    runs = load()
    methods_figure(runs)
    saifi_cap_figure(runs)
    allocation_figure(runs)
    fast_trip_figure(runs)
    print(f"wrote {sorted(p.name for p in OUT.glob('*.png'))}")


if __name__ == "__main__":
    main()
