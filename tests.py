"""Модульные тесты корректности имитационной модели"""
import simpy

import config as C
import model
from model import Depot, Station


def make_station(strategy="level"):
    env = simpy.Environment()
    depot = Depot(env)
    st = Station(env, depot, "АЗС-тест", "АЗС", C.AZS_CAP, C.CHANNELS_AZS, strategy)
    return st, depot


def test_order_refill_whole_sections_and_no_overflow():
    """Заказываются только целые секции, и бак не переполнится"""
    st, depot = make_station()
    st.level["92"] = 5000                       # бак 92 на 30000 л, свободно 25000
    st.order_refill("92")
    assert st.pending["92"] == 4                # 4 секции = 24000 л
    assert st.level["92"] + st.pending["92"] * C.SECTION_VOLUME <= st.cap["92"]
    # повторный заказ при уже заказанном ничего не добавляет
    st.order_refill("92")
    assert st.pending["92"] == 4


def test_order_refill_full_tank_orders_nothing():
    st, depot = make_station()
    st.order_refill("92")                       # бак полный
    assert st.pending["92"] == 0 and not depot.orders


def test_critical_level_threshold():
    """Заказ делается только при запасе <= 25 % емкости, и только один раз."""
    st, depot = make_station("level")
    st.level["95"] = 0.30 * st.cap["95"]        # выше порога - заказа нет
    st.check_critical_level("95")
    assert st.pending["95"] == 0
    st.level["95"] = 0.25 * st.cap["95"]        # на пороге - заказ
    st.check_critical_level("95")
    assert st.pending["95"] > 0
    first = st.pending["95"]
    st.check_critical_level("95")               # повторная проверка - без дубля
    assert st.pending["95"] == first


def test_time_strategy_ignores_level_check():
    st, depot = make_station("time")
    st.level["95"] = 0
    st.check_critical_level("95")
    assert st.pending["95"] == 0                # при стратегии "по времени" не срабатывает


def test_fuel_balance_and_levels_after_run():
    """Баланс топлива: начальный запас + приход - расход = остаток (для обеих стратегий)."""
    old_days = C.SIM_DAYS
    C.SIM_DAYS = 1                              # для скорости проверяем одни сутки
    try:
        for strategy in C.STRATEGIES:
            stations, depot = model.run_simulation(strategy, seed=1)
            for st in stations:
                for fuel, cap in st.cap.items():
                    assert -1e-6 <= st.level[fuel] <= cap + 1e-6
                    initial = cap * C.INITIAL_LEVEL_SHARE
                    diff = initial + st.litres_received[fuel] - st.level[fuel] - st.litres_sold[fuel]
                    # расхождение - только заправки, не завершенные к концу суток
                    assert -1e-6 <= diff <= C.VOLUME_MAX * C.CHANNELS_AZS
    finally:
        C.SIM_DAYS = old_days


def test_same_clients_for_both_strategies():
    """Клиентский поток не зависит от стратегии"""
    old_days = C.SIM_DAYS
    C.SIM_DAYS = 1
    try:
        totals = []
        for strategy in C.STRATEGIES:
            stations, _ = model.run_simulation(strategy, seed=7)
            totals.append(sum(s.total for s in stations))
        assert totals[0] == totals[1]
    finally:
        C.SIM_DAYS = old_days


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("OK  ", t.__name__)
    print(f"\nВсе проверки пройдены: {len(tests)}")
