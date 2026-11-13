"""
model.py - имитационная модель сети АЗС на SimPy.

Устройство модели (процессы):
  * client_generator  - порождает клиентов и распределяет их по станциям;
  * client            - процесс одного клиента: очередь -> заправка -> уезжает;
  * Station           - станция: баки, каналы обслуживания (ТРК), статистика,
                        и "менеджер" (учёт прихода/расхода, выручка, заказ топлива);
  * station_timer     - таймер менеджера для стратегии "по времени";
  * Depot.dispatcher  - диспетчер нефтебазы: превращает заявки в рейсы;
  * Depot.truck_trip  - один рейс бензовоза (2 или 3 секции).
"""
import random
import simpy
import config as C


# ============================================================== станция ====
class Station:
    """АЗС или мини-АЗС. Одновременно выполняет роль менеджера станции."""

    def __init__(self, env, depot, name, kind, capacity, channels, strategy):
        self.env = env
        self.depot = depot
        self.name = name
        self.kind = kind                      # "АЗС" или "ААЗС"
        self.cap = dict(capacity)             # ёмкость баков по видам топлива
        self.level = {f: c * C.INITIAL_LEVEL_SHARE for f, c in capacity.items()}
        self.pending = {f: 0 for f in capacity}   # заказано секций, ещё не привезено
        self.strategy = strategy
        # каналы обслуживания: 4 ТРК x 2 авто = 8 (АЗС), 2 ТРК x 2 авто = 4 (ААЗС)
        self.channels = simpy.Resource(env, capacity=channels)

        # --- статистика менеджера ---
        self.total = 0                        # всего клиентов, приехавших сюда
        self.served = 0                       # обслужено
        self.lost_nofuel = 0                  # уехали: нет нужного топлива
        self.lost_queue = 0                   # уехали: не дождались колонки
        self.revenue = 0.0                    # суммарная выручка, руб
        self.litres_sold = {f: 0.0 for f in capacity}     # расход топлива
        self.litres_received = {f: 0.0 for f in capacity}  # приход топлива

    # ---- заказ топлива (общая часть для обеих стратегий) ----
    def order_refill(self, fuel):
        """Заказать целые секции, которые влезут в свободное место бака."""
        projected = self.level[fuel] + self.pending[fuel] * C.SECTION_VOLUME
        free = self.cap[fuel] - projected
        n_sections = int(free // C.SECTION_VOLUME)    # секция сливается ПОЛНОСТЬЮ
        if n_sections > 0:
            self.pending[fuel] += n_sections
            self.depot.add_order(self, fuel, n_sections)

    # ---- стратегия 1: по критическому уровню ----
    def check_critical_level(self, fuel):
        """Вызывается после каждой заправки: менеджер следит за уровнем."""
        if self.strategy != "level":
            return
        projected = self.level[fuel] + self.pending[fuel] * C.SECTION_VOLUME
        if projected <= C.CRITICAL_SHARE * self.cap[fuel]:
            self.order_refill(fuel)

    # ---- приём топлива от бензовоза ----
    def receive(self, fuel, litres):
        self.level[fuel] += litres
        self.litres_received[fuel] += litres
        self.pending[fuel] -= 1
        assert self.level[fuel] <= self.cap[fuel] + 1e-6, "перелив бака!"


# ============================================================== клиент =====
def client(env, st, fuel, volume):
    """Процесс одного клиента на выбранной станции."""
    st.total += 1

    # 1) Клиент видит, что нужного топлива не хватает - сразу уезжает.
    if st.level[fuel] < volume:
        st.lost_nofuel += 1
        return

    # 2) Ждёт свободный канал (колонку) не дольше MAX_WAIT минут.
    with st.channels.request() as req:
        result = yield req | env.timeout(C.MAX_WAIT)
        if req not in result:
            st.lost_queue += 1
            return

        # 3) Пока стояли в очереди, топливо могли выбрать другие клиенты.
        if st.level[fuel] < volume:
            st.lost_nofuel += 1
            return

        # 4) Заправка: топливо списывается сразу, занимаем канал на объём/скорость.
        st.level[fuel] -= volume
        st.check_critical_level(fuel)         # менеджер проверяет уровень
        yield env.timeout(volume / C.FUELING_SPEED)

        # 5) Учёт менеджера: выручка и расход.
        st.served += 1
        money = volume * C.PRICE[fuel]
        st.revenue += money
        st.litres_sold[fuel] += volume


def client_generator(env, rng, azs_list, mini_list):
    """Единый поток клиентов (допущения 1 и 3) + правила распределения."""
    while True:
        # интервал между клиентами сети = Random(3) / число ТРК
        yield env.timeout(rng.uniform(0, C.INTERVAL_MAX) / C.N_TRK)

        fuel = rng.choice(C.FUELS)                           # тип топлива
        volume = rng.uniform(C.VOLUME_MIN, C.VOLUME_MAX)     # Random(180)+20

        # Распределение клиентов по станциям (из условия):
        if fuel in ("98", "ДТ"):
            group = azs_list                      # 1) 98 и ДТ - только на АЗС
        elif rng.random() < C.SHARE_92_95_TO_AZS:
            group = azs_list                      # 2) 92/95, Random(100) < 50
        else:
            group = mini_list                     # 3) 92/95, Random(100) >= 50
        st = rng.choice(group)                    # конкретная станция в группе
        env.process(client(env, st, fuel, volume))


# ======================================================= стратегия "время" ==
def station_timer(env, rng, st):
    """Менеджер станции по таймеру: раз в REFILL_PERIOD доливает баки."""
    yield env.timeout(rng.uniform(0, C.REFILL_PERIOD))   # случайная фаза
    while True:
        for fuel in st.cap:
            st.order_refill(fuel)
        yield env.timeout(C.REFILL_PERIOD)


# ============================================================ нефтебаза ====
class Depot:
    """Нефтебаза: принимает заявки станций и отправляет бензовозы."""

    def __init__(self, env):
        self.env = env
        self.orders = []                       # заявки: (станция, топливо, секций)
        # trips_by_day[день] = {2: число 2-секц. рейсов, 3: число 3-секц. рейсов}
        self.trips_by_day = [{2: 0, 3: 0} for _ in range(C.SIM_DAYS)]
        self.sections_delivered = 0
        env.process(self.dispatcher())

    def add_order(self, station, fuel, n_sections):
        self.orders.append((station, fuel, n_sections))

    def dispatcher(self):
        """Раз в DISPATCH_WINDOW минут комплектует рейсы из накопленных заявок."""
        while True:
            yield self.env.timeout(C.DISPATCH_WINDOW)
            if not self.orders:
                continue

            # Разворачиваем заявки в отдельные секции: (станция, топливо).
            sections = []
            for st, fuel, n in self.orders:
                sections += [(st, fuel)] * n
            self.orders = []

            # Самые "пустые" баки (меньше доля запаса) везём первыми.
            sections.sort(key=lambda s: s[0].level[s[1]] / s[0].cap[s[1]])

            # Режем на рейсы по 3 секции; остаток 1-2 секции - 2-секц. бензовоз.
            for i in range(0, len(sections), 3):
                load = sections[i:i + 3]
                self.env.process(self.truck_trip(load))

    def truck_trip(self, load):
        """Один рейс: едем на станции маршрута и сливаем секции полностью."""
        trip_type = 3 if len(load) == 3 else 2
        day = min(int(self.env.now // C.DAY), C.SIM_DAYS - 1)
        self.trips_by_day[day][trip_type] += 1       # рейс считаем по дню отправки

        # группируем секции по станциям (одна остановка на станцию)
        route = {}
        for st, fuel in load:
            route.setdefault(st, []).append(fuel)

        yield self.env.timeout(C.TRIP_FIRST_STOP)
        for k, (st, fuels) in enumerate(route.items()):
            if k > 0:
                yield self.env.timeout(C.TRIP_BETWEEN_STOPS)
            for fuel in fuels:
                st.receive(fuel, C.SECTION_VOLUME)
                self.sections_delivered += 1


# ========================================================== запуск модели ===
def run_simulation(strategy, seed):
    """Один прогон на SIM_DAYS суток. Возвращает (станции, нефтебаза)."""
    # Два независимых генератора случайных чисел: клиенты не зависят от стратегии,
    # поэтому обе стратегии сравниваются на ОДИНАКОВЫХ потоках клиентов.
    client_rng = random.Random(seed)
    timer_rng = random.Random(seed + C.TIMER_SEED_OFFSET)
    env = simpy.Environment()
    depot = Depot(env)

    azs_list = [Station(env, depot, f"АЗС-{i + 1}", "АЗС", C.AZS_CAP,
                        C.CHANNELS_AZS, strategy) for i in range(C.N_AZS)]
    mini_list = [Station(env, depot, f"ААЗС-{i + 1}", "ААЗС", C.MINI_CAP,
                         C.CHANNELS_MINI, strategy) for i in range(C.N_MINI)]

    env.process(client_generator(env, client_rng, azs_list, mini_list))
    if strategy == "time":
        for st in azs_list + mini_list:
            env.process(station_timer(env, timer_rng, st))

    env.run(until=C.SIM_DAYS * C.DAY)
    return azs_list + mini_list, depot
