"""Имитационная модель сети АЗС на SimPy.
Описывает процессы генерации клиентов, обслуживания на станциях,
работы менеджеров и доставки топлива бензовозами с нефтебазы.
"""
import random
import simpy
import config as C


class Station:
    """Станция  с баками топлива и каналами обслуживания"""

    def __init__(self, env, depot, name, kind, capacity, channels, strategy):
        self.env = env
        self.depot = depot
        self.name = name
        self.kind = kind                      # АЗС или ААЗС
        self.cap = dict(capacity)             # eмкость баков по видам топлива
        self.level = {f: c * C.INITIAL_LEVEL_SHARE for f, c in capacity.items()}
        self.pending = {f: 0 for f in capacity}   # заказано секций в пути
        self.strategy = strategy
        self.channels = simpy.Resource(env, capacity=channels)

        self.total = 0                        # всего прибывших клиентов
        self.served = 0                       # успешно обслужено
        self.lost_nofuel = 0                  # отказов из-за отсутствия топлива
        self.lost_queue = 0                   # отказов из-за переполнения очереди
        self.revenue = 0.0                    # суммарная выручка, руб
        self.litres_sold = {f: 0.0 for f in capacity}
        self.litres_received = {f: 0.0 for f in capacity}

    def order_refill(self, fuel):
        """Заказ целых секций под свободный объем бака"""
        projected = self.level[fuel] + self.pending[fuel] * C.SECTION_VOLUME
        free = self.cap[fuel] - projected
        n_sections = int(free // C.SECTION_VOLUME)  # секция бензовоза сливается только полностью
        if n_sections > 0:
            self.pending[fuel] += n_sections
            self.depot.add_order(self, fuel, n_sections)

    def check_critical_level(self, fuel):
        """Проверка порога остатка топлива для стратегии по критическому уровню"""
        if self.strategy != "level":
            return
        projected = self.level[fuel] + self.pending[fuel] * C.SECTION_VOLUME
        if projected <= C.CRITICAL_SHARE * self.cap[fuel]:
            self.order_refill(fuel)

    def receive(self, fuel, litres):
        self.level[fuel] += litres
        self.litres_received[fuel] += litres
        self.pending[fuel] -= 1
        assert self.level[fuel] <= self.cap[fuel] + 1e-6, "перелив бака"


def client(env, st, fuel, volume):
    """Процесс обслуживания клиента на выбранной станции"""
    st.total += 1

    if st.level[fuel] < volume:
        st.lost_nofuel += 1
        return

    with st.channels.request() as req:
        result = yield req | env.timeout(C.MAX_WAIT)
        if req not in result:
            st.lost_queue += 1
            return

        # проверка остатка топлива после ожидания в очереди
        if st.level[fuel] < volume:
            st.lost_nofuel += 1
            return

        st.level[fuel] -= volume
        st.check_critical_level(fuel)
        yield env.timeout(volume / C.FUELING_SPEED)

        st.served += 1
        st.revenue += volume * C.PRICE[fuel]
        st.litres_sold[fuel] += volume


def client_generator(env, rng, azs_list, mini_list):
    """Генерация общего потока клиентов с распределением по станциям сети"""
    while True:
        # Интервал между приходами по всей сети: Random(3) / N_TRK
        yield env.timeout(rng.uniform(0, C.INTERVAL_MAX) / C.N_TRK)

        fuel = rng.choice(C.FUELS)
        volume = rng.uniform(C.VOLUME_MIN, C.VOLUME_MAX)

        if fuel in ("98", "ДТ"):
            group = azs_list
        elif rng.random() < C.SHARE_92_95_TO_AZS:
            group = azs_list
        else:
            group = mini_list
        st = rng.choice(group)
        env.process(client(env, st, fuel, volume))


def station_timer(env, rng, st):
    """Таймер периодического пополнения запасов для стратегии по времени"""
    yield env.timeout(rng.uniform(0, C.REFILL_PERIOD))   # случайный сдвиг начальной фазы
    while True:
        for fuel in st.cap:
            st.order_refill(fuel)
        yield env.timeout(C.REFILL_PERIOD)


class Depot:
    """Агрегация заявок станций и отправка бензовозов"""

    def __init__(self, env):
        self.env = env
        self.orders = []
        self.trips_by_day = [{2: 0, 3: 0} for _ in range(C.SIM_DAYS)]
        self.sections_delivered = 0
        env.process(self.dispatcher())

    def add_order(self, station, fuel, n_sections):
        self.orders.append((station, fuel, n_sections))

    def dispatcher(self):
        """Периодическая комплектация и отправка рейсов по накопленным заявкам"""
        while True:
            yield self.env.timeout(C.DISPATCH_WINDOW)
            if not self.orders:
                continue

            sections = []
            for st, fuel, n in self.orders:
                sections += [(st, fuel)] * n
            self.orders = []

            # приоритет отгрузки отдается бакам с наименьшей относительной заполненностью
            sections.sort(key=lambda s: s[0].level[s[1]] / s[0].cap[s[1]])

            # комплектация 3-секционных рейсов; неделимый остаток в 1-2 секции вывозится 2-секционным
            for i in range(0, len(sections), 3):
                load = sections[i:i + 3]
                self.env.process(self.truck_trip(load))

    def truck_trip(self, load):
        """Рейс бензовоза с последовательным сливом секций на станциях маршрута"""
        trip_type = 3 if len(load) == 3 else 2
        day = min(int(self.env.now // C.DAY), C.SIM_DAYS - 1)
        self.trips_by_day[day][trip_type] += 1

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


def run_simulation(strategy, seed):
    """Один прогон имитационной модели на SIM_DAYS суток"""
    # раздельные генераторы случайных чисел обеспечивают одинаковый входной поток клиентов
    # для корректного сравнительного анализа стратегий снабжения
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
