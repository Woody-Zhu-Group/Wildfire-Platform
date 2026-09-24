"""
analysis.py
-----------
Extended analysis on top of the core cNHPP baseline:
  1. Geographic adjacency matrix (distance-based neighbors) vs substation-based
  2. Extra validation metrics: top-K precision, AUC
  3. Monthly performance breakdown
  4. Calibration plot
  5. Spatial risk map (circuits colored by estimated intensity)

Run after the main model fit. Imports from data_prep and models.
"""

import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.spatial import cKDTree
from scipy.stats import rankdata

# Plotting is optional: matplotlib is imported only by the plot_* functions
# and is listed in requirements-analysis.txt, not requirements.txt.


def _pyplot():
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            "analysis.py plots need matplotlib: "
            "pip install -r requirements-analysis.txt"
        ) from exc
    return plt


# ─────────────────────────────────────────────
# 1. GEOGRAPHIC ADJACENCY
# ─────────────────────────────────────────────

def build_knn_adjacency(
    midpoints_csv: str,
    k: int = 8,
) -> sp.csr_matrix:
    """
    Build N×N adjacency using k nearest neighbors per circuit (by distance).
    Much sparser and faster than radius-based when circuits cluster densely.
    Equal weights 1/(k+1), self-loop included.
    """
    print(f"[KNN-ADJ] Building k-nearest-neighbor adjacency (k={k}) ...")
    mid = pd.read_csv(midpoints_csv).sort_values("seg_idx").reset_index(drop=True)
    N = len(mid)

    R = 111_320.0
    lat0 = np.deg2rad(mid["mid_lat"].mean())
    xy = np.column_stack([
        np.deg2rad(mid["mid_lon"].values) * np.cos(lat0) * R,
        np.deg2rad(mid["mid_lat"].values) * R,
    ])

    tree = cKDTree(xy)
    _, idx = tree.query(xy, k=k + 1)   # k+1 because nearest is self

    W = sp.lil_matrix((N, N), dtype=np.float32)
    w = 1.0 / (k + 1)
    for i in range(N):
        for j in idx[i]:
            W[i, j] = w
    W = W.tocsr()
    print(f"[KNN-ADJ]   W: {N}×{N}  nnz={W.nnz}  neighbors per circuit={k+1}")
    return W


def build_geographic_adjacency(
    midpoints_csv: str,
    radius_km: float = 10.0,
) -> sp.csr_matrix:
    """
    Build N×N adjacency where circuits within radius_km of each other are
    neighbors. Equal weights 1/|neighbors|, self-loop included.

    This replaces substation-based adjacency. Geographically close circuits
    genuinely share fire conditions, unlike electrically-connected ones that
    may point in different directions.
    """
    print(f"[GEO-ADJ] Building distance-based adjacency (r={radius_km} km) ...")
    mid = pd.read_csv(midpoints_csv).sort_values("seg_idx").reset_index(drop=True)
    N = len(mid)

    # Project to metres (equirectangular, fine for CA extent)
    R = 111_320.0
    lat0 = np.deg2rad(mid["mid_lat"].mean())
    xy = np.column_stack([
        np.deg2rad(mid["mid_lon"].values) * np.cos(lat0) * R,
        np.deg2rad(mid["mid_lat"].values) * R,
    ])

    tree = cKDTree(xy)
    pairs = tree.query_pairs(r=radius_km * 1000, output_type="ndarray")

    # Build neighbor lists (symmetric + self)
    from collections import defaultdict
    nbrs = defaultdict(set)
    for i in range(N):
        nbrs[i].add(i)
    for a, b in pairs:
        nbrs[a].add(b)
        nbrs[b].add(a)

    W = sp.lil_matrix((N, N), dtype=np.float32)
    for i in range(N):
        deg = len(nbrs[i])
        w = 1.0 / deg
        for j in nbrs[i]:
            W[i, j] = w
    W = W.tocsr()

    avg_deg = np.mean([len(nbrs[i]) for i in range(N)])
    print(f"[GEO-ADJ]   W: {N}×{N}  nnz={W.nnz}  avg neighbors={avg_deg:.1f}")
    return W


# ─────────────────────────────────────────────
# 2. EXTRA METRICS
# ─────────────────────────────────────────────

def topk_precision(E: np.ndarray, log_lambda: np.ndarray, k_frac: float = 0.05) -> float:
    """
    Among the top k_frac fraction of highest-risk circuit-days, what fraction
    had an actual fire? Compared to base rate, shows lift.
    """
    N, T = E.shape
    k = max(1, int(N * k_frac))
    hits, total_top = 0, 0
    for t in range(T):
        if E[:, t].sum() == 0:
            continue
        top_idx = np.argsort(log_lambda[:, t])[-k:]
        hits += E[top_idx, t].sum()
        total_top += k
    return hits / total_top if total_top else 0.0


def binary_auc(y: np.ndarray, score: np.ndarray) -> float:
    """ROC AUC for a binary outcome, with tied scores counted as one half.

    The Mann-Whitney form on average ranks, which is what
    sklearn.metrics.roc_auc_score returns for binary labels.
    """
    y = np.asarray(y).astype(bool)
    n_pos = int(y.sum())
    n_neg = int(y.size - n_pos)
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = rankdata(np.asarray(score, dtype=np.float64))
    return float((ranks[y].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def daily_auc(E: np.ndarray, log_lambda: np.ndarray) -> float:
    """
    Pooled AUC: treat every circuit-day as a binary outcome (fire / no fire)
    and the predicted log-intensity as the score.
    """
    y = (E.T.ravel() > 0).astype(int)       # (T*N,)
    s = log_lambda.T.ravel()
    return binary_auc(y, s)


def all_metrics(E, hpp, nhpp, cnhpp) -> pd.DataFrame:
    """Build a metrics comparison table across the three models."""
    rows = []
    base_rate = E.sum() / E.size
    for name, res in [("HPP", hpp), ("NHPP", nhpp), ("cNHPP", cnhpp)]:
        ll = res.log_lambda
        rows.append({
            "model": name,
            "log_likelihood": res.log_likelihood,
            "top5%_precision": topk_precision(E, ll, 0.05),
            "top1%_precision": topk_precision(E, ll, 0.01),
            "AUC": daily_auc(E, ll),
        })
    df = pd.DataFrame(rows)
    df["lift_top5%"] = df["top5%_precision"] / base_rate
    print(f"\n[METRICS] base fire rate = {base_rate:.2e}")
    print(df.to_string(index=False))
    return df


# ─────────────────────────────────────────────
# 3. MONTHLY BREAKDOWN
# ─────────────────────────────────────────────

def monthly_performance(E, log_lambda, date_range) -> pd.DataFrame:
    """Median percentile rank of fire circuits, broken down by month."""
    N, T = E.shape
    months = date_range.month
    rows = []
    for m in range(1, 13):
        cols = np.where(months == m)[0]
        pcts = []
        for t in cols:
            for i in range(N):
                if E[i, t] > 0:
                    col = log_lambda[:, t]
                    pcts.append(np.mean(col <= col[i]) * 100)
        rows.append({
            "month": date_range[cols][0].strftime("%b") if len(cols) else str(m),
            "n_fires": int(sum(E[:, t].sum() for t in cols)),
            "median_pct": np.median(pcts) if pcts else np.nan,
        })
    df = pd.DataFrame(rows)
    print("\n[MONTHLY]")
    print(df.to_string(index=False))
    return df


def plot_monthly(df_monthly, save_path=None):
    plt = _pyplot()
    fig, ax1 = plt.subplots(figsize=(10, 4))
    ax2 = ax1.twinx()
    x = range(len(df_monthly))
    ax1.bar(x, df_monthly["n_fires"], color="#D3D1C7", alpha=0.6, label="fires")
    ax2.plot(x, df_monthly["median_pct"], "o-", color="#534AB7",
             linewidth=2, markersize=7, label="median percentile")
    ax2.axhline(50, color="gray", linestyle=":", alpha=0.7)
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(df_monthly["month"])
    ax1.set_ylabel("Number of fires", fontsize=11)
    ax2.set_ylabel("Median percentile rank (%)", fontsize=11, color="#534AB7")
    ax2.set_ylim(0, 105)
    ax1.set_title("Monthly fire counts and model performance", fontsize=13)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150); print(f"[VIZ] Saved: {save_path}")
    plt.close()


# ─────────────────────────────────────────────
# 4. CALIBRATION PLOT
# ─────────────────────────────────────────────

def plot_calibration(E, log_lambda, n_bins=10, save_path=None):
    plt = _pyplot()
    """
    Bin circuit-days by predicted intensity, compare mean predicted rate
    to observed fire rate in each bin. Well-calibrated → diagonal.
    """
    lam = np.exp(log_lambda).T.ravel()
    y   = (E.T.ravel() > 0).astype(float)

    order = np.argsort(lam)
    lam_s, y_s = lam[order], y[order]
    bins = np.array_split(np.arange(len(lam_s)), n_bins)

    pred = [lam_s[b].mean() for b in bins]
    obs  = [y_s[b].mean()   for b in bins]

    fig, ax = plt.subplots(figsize=(6, 6))
    lim = max(max(pred), max(obs)) * 1.1
    ax.plot([0, lim], [0, lim], "--", color="gray", label="perfect calibration")
    ax.plot(pred, obs, "o-", color="#534AB7", markersize=7, label="model")
    ax.set_xlabel("Mean predicted fire rate", fontsize=11)
    ax.set_ylabel("Observed fire rate", fontsize=11)
    ax.set_title("Calibration (binned by predicted risk)", fontsize=13)
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150); print(f"[VIZ] Saved: {save_path}")
    plt.close()


# ─────────────────────────────────────────────
# 5. SPATIAL RISK MAP
# ─────────────────────────────────────────────

def plot_spatial_risk(
    shp_path: str,
    circuits_df: pd.DataFrame,
    log_lambda: np.ndarray,
    events_df: pd.DataFrame = None,
    save_path: str = None,
):
    """
    Plot all circuits colored by mean estimated intensity over the study period.
    Overlay actual fire locations if events_df provided.
    """
    import geopandas as gpd

    plt = _pyplot()

    print("[MAP] Building spatial risk map ...")
    gdf = gpd.read_file(shp_path).to_crs("EPSG:4326")
    gdf = gdf.merge(circuits_df[["circuitid", "seg_idx"]], on="circuitid", how="left")

    mean_intensity = np.exp(log_lambda).mean(axis=1)   # (N,)
    gdf["risk"] = gdf["seg_idx"].map(
        {i: mean_intensity[i] for i in range(len(mean_intensity))}
    )

    fig, ax = plt.subplots(figsize=(10, 12))
    gdf.plot(column="risk", ax=ax, cmap="YlOrRd", linewidth=0.8,
             legend=True, legend_kwds={"label": "Mean estimated fire intensity",
                                       "shrink": 0.5})

    if events_df is not None:
        ev = events_df.dropna(subset=["seg_idx"])
        ax.scatter(ev["lon"], ev["lat"], s=12, c="blue", alpha=0.5,
                   edgecolors="none", label=f"actual fires (n={len(ev)})")
        ax.legend(loc="upper right")

    ax.set_title("Estimated wildfire risk across PG&E GNA circuits (2024)",
                 fontsize=14)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"[VIZ] Saved: {save_path}")
    plt.close()
