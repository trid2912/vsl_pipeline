#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Draw topic and segment-length (word count) distributions for the recordable
segment pool (846 standard/hard segments in the recording script)."""
import csv, os, statistics
from collections import Counter
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

# Run relative to the repo root (the dir containing final_corpus), wherever this script lives.
_R = os.path.abspath(__file__)
while _R != os.path.dirname(_R) and not os.path.isdir(os.path.join(_R, 'final_corpus')):
    _R = os.path.dirname(_R)
os.chdir(_R)

REC = "final_corpus/recording/final_recording_script.csv"
OUT_DIR = "final_corpus/recording"

# palette (dataviz reference, light surface)
SURFACE, INK, INK2 = "#fcfcfb", "#0b0b0b", "#52514e"
BLUE, GRID = "#2a78d6", "#e6e5e2"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 11,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
})

# ---- load unique recordable segments ----
# Vietnamese domain -> English label
TOPIC_EN = {
    "Lịch sử": "History", "Xã hội": "Society", "Thời tiết và Mùa": "Weather & Seasons",
    "Công việc": "Work", "Gia đình": "Family", "Sức khỏe": "Health",
    "Học đường": "School", "Động vật": "Animals", "Ăn uống": "Food & Drink",
    "Đời sống hàng ngày": "Daily life", "Du lịch": "Travel",
    "Phương tiện di chuyển": "Transport", "Sở thích": "Hobbies",
    "Giao tiếp Xã hội": "Social interaction", "Giải trí": "Entertainment",
    "Thiên nhiên": "Nature", "Mua sắm": "Shopping", "Màu sắc": "Colors",
}

seen = {}
for r in csv.DictReader(open(REC, encoding="utf-8-sig")):
    if r["length_bucket"] in ("standard", "hard"):
        seen.setdefault(r["segment_id"], r)
segs = list(seen.values())
N = len(segs)
words = [int(r["segmented_word_count"]) for r in segs]
cats = Counter(TOPIC_EN.get(r["domain"], r["domain"]) for r in segs)

def style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)

# ============================ 1) TOPIC DISTRIBUTION ============================
items = cats.most_common()          # sorted desc
labels = [k for k, _ in items][::-1]  # reverse so largest is on top
vals = [v for _, v in items][::-1]
fig, ax = plt.subplots(figsize=(9, 7.2))
bars = ax.barh(labels, vals, color=BLUE, height=0.7, zorder=3)
ax.bar_label(bars, padding=4, fontsize=10, color=INK,
             labels=[f"{v}  ({v/N*100:.0f}%)" for v in vals])
ax.xaxis.grid(True, color=GRID, zorder=0)
ax.set_axisbelow(True)
ax.set_xlim(0, max(vals) * 1.16)
ax.set_xlabel("Number of segments")
style(ax)
ax.tick_params(length=0)
fig.suptitle("Segment distribution by topic", x=0.02, ha="left", fontsize=15, fontweight="bold")
ax.set_title(f"Recording pool: {N} standard/hard segments  ·  {len(items)} topics",
             loc="left", fontsize=10.5, color=INK2, pad=10)
fig.tight_layout(rect=(0, 0, 1, 0.97))
fig.savefig(f"{OUT_DIR}/dist_topic.png", dpi=150)
plt.close(fig)

# ======================= 2) SEGMENT LENGTH (WORD COUNT) =======================
med = statistics.median(words); mean = statistics.mean(words)
bins = list(range(70, 181, 10))
fig, ax = plt.subplots(figsize=(9, 5.2))
n, _, patches = ax.hist(words, bins=bins, color=BLUE, edgecolor=SURFACE, linewidth=1.5, zorder=3)
ax.axvline(med, color=INK, linestyle="--", linewidth=1.5, zorder=4)
ax.text(med + 1.5, max(n) * 0.96, f"median {med:.0f} words", color=INK, fontsize=10, va="top")
ax.yaxis.grid(True, color=GRID, zorder=0)
ax.set_axisbelow(True)
ax.set_xticks(bins)
ax.set_xlabel("Words per segment  (≈ signing seconds × 0.75)")
ax.set_ylabel("Number of segments")
ax.yaxis.set_major_locator(MaxNLocator(integer=True))
style(ax)
ax.tick_params(length=0)
fig.suptitle("Segment length distribution (word count)", x=0.02, ha="left", fontsize=15, fontweight="bold")
ax.set_title(f"n={N}  ·  median {med:.0f} · mean {mean:.0f} · range {min(words)}–{max(words)} words "
             f"(standard 70–110, hard 100–150)",
             loc="left", fontsize=10, color=INK2, pad=10)
fig.tight_layout(rect=(0, 0, 1, 0.96))
fig.savefig(f"{OUT_DIR}/dist_segment_length.png", dpi=150)
plt.close(fig)

print("wrote:", f"{OUT_DIR}/dist_topic.png", "and", f"{OUT_DIR}/dist_segment_length.png")
print(f"N={N}  words median={med:.0f} mean={mean:.1f} range={min(words)}-{max(words)}")
print("top topics:", items[:5])
