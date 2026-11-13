"""
plots.py - графики для отчёта. Запускать ПОСЛЕ run.py:  python plots.py
Читает results/*.csv и сохраняет картинки в results/*.png
"""
import csv
import os

import matplotlib
matplotlib.use("Agg")          # рисуем в файл, без окна
import matplotlib.pyplot as plt

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


def read(name):
    with open(os.path.join(OUT, name), encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def plot_trips():
    """Рейсы бензовозов по дням: 2- и 3-секционные (столбики с накоплением)."""
    rows = read("trips_by_day.csv")
    strategies = sorted({r["стратегия"] for r in rows})
    fig, axes = plt.subplots(1, len(strategies), figsize=(12, 4.5), sharey=True)
    for ax, strat in zip(axes, strategies):
        data = [r for r in rows if r["стратегия"] == strat]
        days = [int(r["день"]) for r in data]
        t2 = [float(r["рейсов_2_секц"]) for r in data]
        t3 = [float(r["рейсов_3_секц"]) for r in data]
        ax.bar(days, t2, label="2-секционные")
        ax.bar(days, t3, bottom=t2, label="3-секционные")
        ax.set_title(f"Стратегия: {strat}")
        ax.set_xlabel("день")
        ax.set_xticks(days)
    axes[0].set_ylabel("число рейсов")
    axes[0].legend()
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "trips_by_day.png"), dpi=150)
    plt.close(fig)


def plot_share_and_revenue():
    """Доля обслуженных и выручка: АЗС vs ААЗС, две стратегии рядом."""
    rows = read("summary.csv")
    groups = ["АЗС", "ААЗС"]
    strategies = sorted({r["стратегия"] for r in rows})
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.5))
    width = 0.35
    for i, strat in enumerate(strategies):
        sel = {r["группа"]: r for r in rows if r["стратегия"] == strat}
        x = [g + i * width for g in range(len(groups))]
        a1.bar(x, [float(sel[g]["доля_обслуженных"]) * 100 for g in groups],
               width, label=strat)
        a2.bar(x, [float(sel[g]["выручка_руб"]) / 1e6 for g in groups],
               width, label=strat)
    for ax, title, yl in ((a1, "Доля обслуженных клиентов", "%"),
                          (a2, "Выручка за 10 суток", "млн руб")):
        ax.set_xticks([g + width / 2 for g in range(len(groups))])
        ax.set_xticklabels(groups)
        ax.set_title(title)
        ax.set_ylabel(yl)
    a1.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "share_and_revenue.png"), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    plot_trips()
    plot_share_and_revenue()
    print("Графики сохранены в", OUT)
