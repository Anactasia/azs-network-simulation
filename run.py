"""Скрипт проведения серии имитационных экспериментов.
Выполняет N_RUNS прогонов для каждой стратегии снабжения, рассчитывает
выборочные средние и 95% доверительные интервалы, выводит сводные данные
и сохраняет результаты в каталог results/.
"""
import csv
import math
import os
import statistics
import time

import config as C
from model import run_simulation

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# Критические значения t-распределения Стьюдента (95 %, двусторонний) для df = 1..30
T_CRIT = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
          2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
          2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def summarize(stations, depot):
    """Собирает показатели одного прогона по группам АЗС / ААЗС."""
    res = {}
    for kind in ("АЗС", "ААЗС"):
        group = [s for s in stations if s.kind == kind]
        total = sum(s.total for s in group)
        served = sum(s.served for s in group)
        res[kind] = {
            "total": total,
            "served": served,
            "share": served / total if total else 0.0,
            "lost_nofuel": sum(s.lost_nofuel for s in group),
            "lost_queue": sum(s.lost_queue for s in group),
            "revenue": sum(s.revenue for s in group),
            "litres": sum(sum(s.litres_sold.values()) for s in group),
        }
    res["trips"] = depot.trips_by_day               # рейсы по дням
    res["trips_total"] = sum(d[2] + d[3] for d in depot.trips_by_day)
    res["stations"] = stations
    return res


def mean(values):
    return statistics.fmean(values)


def ci(values):
    """Полуширина 95%-го доверительного интервала среднего."""
    values = list(values)
    n = len(values)
    if n < 2:
        return 0.0
    t = T_CRIT[n - 2] if n - 1 <= len(T_CRIT) else 1.96
    return t * statistics.stdev(values) / math.sqrt(n)


def group_stat(runs, kind, key):
    vals = [r[kind][key] for r in runs]
    return mean(vals), ci(vals)


def main():
    os.makedirs(OUT, exist_ok=True)
    all_results = {}

    for strategy, title in C.STRATEGIES.items():
        runs = []
        for r in range(C.N_RUNS):
            t0 = time.time()
            stations, depot = run_simulation(strategy, C.BASE_SEED + r)
            runs.append(summarize(stations, depot))
            print(f"  [{title}] прогон {r + 1}/{C.N_RUNS} готов за {time.time() - t0:.1f} с")
        all_results[strategy] = runs

    # Вывод сводных результатов в консоль
    for strategy, title in C.STRATEGIES.items():
        runs = all_results[strategy]
        print("\n" + "=" * 78)
        print(f"СТРАТЕГИЯ СНАБЖЕНИЯ: {title}  (среднее ± 95% ДИ по {C.N_RUNS} прогонам, {C.SIM_DAYS} сут.)")
        print("=" * 78)
        print(f"{'':6}{'клиентов':>10}{'обслужено':>11}{'доля, %':>14}"
              f"{'нет топл.':>11}{'очередь':>9}{'выручка, млн руб':>22}")
        for kind in ("АЗС", "ААЗС"):
            tot, _ = group_stat(runs, kind, "total")
            srv, _ = group_stat(runs, kind, "served")
            sh, sh_ci = group_stat(runs, kind, "share")
            nf, _ = group_stat(runs, kind, "lost_nofuel")
            q, _ = group_stat(runs, kind, "lost_queue")
            rv, rv_ci = group_stat(runs, kind, "revenue")
            print(f"{kind:6}{tot:>10.0f}{srv:>11.0f}{sh * 100:>8.2f}±{sh_ci * 100:<5.2f}"
                  f"{nf:>11.0f}{q:>9.0f}{rv / 1e6:>14,.1f}±{rv_ci / 1e6:<6.1f}")

        tt = [r["trips_total"] for r in runs]
        print(f"\nВсего рейсов за {C.SIM_DAYS} сут.: {mean(tt):.0f} ± {ci(tt):.0f}")
        print("Рейсы бензовозов по дням (среднее по прогонам; 2-секц. / 3-секц. / всего):")
        print(f"{'день':>5}{'2-секц.':>9}{'3-секц.':>9}{'всего':>8}")
        for d in range(C.SIM_DAYS):
            t2 = mean(r["trips"][d][2] for r in runs)
            t3 = mean(r["trips"][d][3] for r in runs)
            print(f"{d + 1:>5}{t2:>9.1f}{t3:>9.1f}{t2 + t3:>8.1f}")

    # Сохранение результатов в формате CSV
    # Итоги по группам станций (среднее и полуширина 95% ДИ)
    with open(os.path.join(OUT, "summary.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["стратегия", "группа", "клиентов", "обслужено", "доля_обслуженных",
                    "доля_ДИ95", "потеряно_нет_топлива", "потеряно_очередь",
                    "выручка_руб", "выручка_ДИ95_руб", "продано_литров"])
        for strategy, title in C.STRATEGIES.items():
            runs = all_results[strategy]
            for kind in ("АЗС", "ААЗС"):
                g = lambda k: group_stat(runs, kind, k)
                w.writerow([title, kind, round(g("total")[0]), round(g("served")[0]),
                            round(g("share")[0], 4), round(g("share")[1], 4),
                            round(g("lost_nofuel")[0]), round(g("lost_queue")[0]),
                            round(g("revenue")[0]), round(g("revenue")[1]),
                            round(g("litres")[0])])

    # Статистика рейсов бензовозов по дням
    with open(os.path.join(OUT, "trips_by_day.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["стратегия", "день", "рейсов_2_секц", "рейсов_3_секц", "всего"])
        for strategy, title in C.STRATEGIES.items():
            runs = all_results[strategy]
            for d in range(C.SIM_DAYS):
                t2 = mean(r["trips"][d][2] for r in runs)
                t3 = mean(r["trips"][d][3] for r in runs)
                w.writerow([title, d + 1, round(t2, 1), round(t3, 1), round(t2 + t3, 1)])

    # Детальная статистика по отдельным станциям
    with open(os.path.join(OUT, "stations.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["стратегия", "станция", "клиентов", "обслужено", "доля", "выручка_руб"])
        for strategy, title in C.STRATEGIES.items():
            for s in all_results[strategy][0]["stations"]:
                share = s.served / s.total if s.total else 0
                w.writerow([title, s.name, s.total, s.served, round(share, 4), round(s.revenue)])

    print(f"\nCSV сохранены в: {OUT}")
    return all_results


if __name__ == "__main__":
    main()
