"""
make_analysis_figure.py
=======================
Three-panel analytical figure for the Pakistan seismicity report, drawn in the
same restrained white/violet palette as the maps.

Panels
------
(a) Events per year, log scale — exposes catalogue heterogeneity (the 1973 jump).
(b) Gutenberg-Richter cumulative frequency-magnitude, with the Aki (1965)
    maximum-likelihood b-value fitted above the completeness magnitude.
(c) Hypocentral depth distribution — isolates the Hindu Kush intermediate nest.

Output: outputs/pakistan_seismicity_analysis.png
"""

import os
import math

import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams

BASE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(BASE, "data", "analysis", "usgs_pakistan_catalogue.csv")
OUT = os.path.join(BASE, "outputs", "pakistan_seismicity_analysis.png")

VIOLET = "#6d28d9"
VIOLET_L = "#a78bfa"
INK = "#1a1a20"
GREY = "#8b8f9c"

rcParams.update({
    "font.family": "serif",
    "font.serif": ["Georgia", "Cambria", "DejaVu Serif"],
    "axes.edgecolor": "#c3c6cf",
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": "#4e4e58",
    "ytick.color": "#4e4e58",
    "axes.grid": True,
    "grid.color": "#e6e8ee",
    "grid.linewidth": 0.8,
})

con = duckdb.connect()
con.execute(f"""
    CREATE VIEW q AS
    SELECT *, EXTRACT(year FROM time) AS yr
    FROM read_csv_auto('{CSV.replace(os.sep, "/")}', header=true)
    WHERE type = 'earthquake'
""")

fig, axes = plt.subplots(1, 3, figsize=(16.5, 4.9), dpi=200)
fig.patch.set_facecolor("white")

# ---------------------------------------------------------------- (a) per year
yrs = con.execute("SELECT yr, COUNT(*) FROM q GROUP BY yr ORDER BY yr").fetchall()
x = np.array([r[0] for r in yrs], float)
y = np.array([r[1] for r in yrs], float)
ax = axes[0]
ax.fill_between(x, 0.7, y, color=VIOLET_L, alpha=0.55, linewidth=0)
ax.plot(x, y, color=VIOLET, lw=1.4)
ax.set_yscale("log")
ax.set_xlim(1900, 2026)
ax.set_ylim(0.7, 1400)
ax.axvline(1973, color=INK, lw=1.0, ls="--", alpha=0.75)
ax.annotate("1973: global digital\nnetwork — detection\nthreshold falls to ~M3",
            xy=(1973, 300), xytext=(1913, 430), fontsize=8.5, color=INK,
            arrowprops=dict(arrowstyle="->", color=INK, lw=0.9))
ax.annotate("2005 Kashmir\naftershocks (912)", xy=(2005, 912), xytext=(1979, 1000),
            fontsize=8.5, color=VIOLET,
            arrowprops=dict(arrowstyle="->", color=VIOLET, lw=0.9))
ax.set_title("(a)  Recorded events per year", fontsize=11.5, loc="left", pad=9)
ax.set_xlabel("Year", fontsize=9.5)
ax.set_ylabel("Events (log scale)", fontsize=9.5)

# ----------------------------------------------- (b) Gutenberg-Richter, b-value
mags = np.array([r[0] for r in con.execute(
    "SELECT mag FROM q WHERE yr >= 1990 AND mag IS NOT NULL").fetchall()], float)
bins = np.arange(3.0, 8.3, 0.1)
cum = np.array([(mags >= b).sum() for b in bins], float)
ok = cum > 0
MC = 4.5
sel = mags[mags >= MC]
b_val = math.log10(math.e) / (sel.mean() - (MC - 0.05))
a_val = math.log10(len(sel)) + b_val * MC

ax = axes[1]
ax.scatter(bins[ok], cum[ok], s=17, facecolor="white", edgecolor=VIOLET, lw=1.0, zorder=3)
xs = np.linspace(MC, 8.2, 50)
ax.plot(xs, 10 ** (a_val - b_val * xs), color=INK, lw=1.5, zorder=4,
        label=f"Aki (1965) MLE fit,  $b$ = {b_val:.2f}")
ax.axvline(MC, color=GREY, lw=1.0, ls=":")
ax.text(MC + 0.06, 1.6, "completeness\n$M_c \\approx 4.5$", fontsize=8.5, color=GREY)
ax.fill_betweenx([0.8, 1e4], 3.0, MC, color="#f2f0f7", zorder=0)
ax.text(3.55, 2400, "under-recorded", fontsize=8.5, color=GREY, ha="center")
ax.set_yscale("log")
ax.set_xlim(3.0, 8.3)
ax.set_ylim(0.8, 1e4)
ax.legend(frameon=False, fontsize=9, loc="upper right")
ax.set_title("(b)  Magnitude–frequency, 1990–2025", fontsize=11.5, loc="left", pad=9)
ax.set_xlabel("Magnitude $M$", fontsize=9.5)
ax.set_ylabel("Cumulative $N(\\geq M)$", fontsize=9.5)

# ------------------------------------------------------------- (c) depth stack
dep = np.array([r[0] for r in con.execute(
    "SELECT depth FROM q WHERE depth IS NOT NULL").fetchall()], float)
ax = axes[2]
ax.hist(dep, bins=np.arange(0, 320, 10), orientation="horizontal",
        color=VIOLET_L, edgecolor=VIOLET, linewidth=0.6)
ax.invert_yaxis()
ax.set_ylim(310, 0)
ax.axhspan(150, 300, color=VIOLET, alpha=0.10, zorder=0)
ax.text(1620, 225, "Hindu Kush\nintermediate-depth nest\n27.1% of all events\nmean 203 km",
        fontsize=8.8, color=VIOLET, va="center")
ax.annotate("Crustal seismicity —\n41.9% shallower than 35 km",
            xy=(2400, 25), xytext=(1300, 72), fontsize=8.8, color=INK, va="center",
            arrowprops=dict(arrowstyle="->", color=INK, lw=0.9))
ax.set_title("(c)  Hypocentral depth distribution", fontsize=11.5, loc="left", pad=9)
ax.set_xlabel("Number of events", fontsize=9.5)
ax.set_ylabel("Depth (km)", fontsize=9.5)

for a in axes:
    a.spines["top"].set_visible(False)
    a.spines["right"].set_visible(False)

fig.tight_layout(pad=1.7)
fig.savefig(OUT, dpi=200, facecolor="white", bbox_inches="tight")
print(f"[+] {OUT}")
print(f"    b-value = {b_val:.3f}  (n = {len(sel)} at M >= {MC})")
