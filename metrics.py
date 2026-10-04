"""
Yengil, xotirada saqlanadigan kuzatuv ko'rsatkichlari (metrics).

Maqsad — "bot haqiqatan ham tez ishlayaptimi?" degan savolga javob berish:
- railway.uz'ga necha so'rov ketdi, nechtasi qayta urinish/429/timeout bo'ldi;
- qidiruv navbatida necha soniya kutildi;
- bitta tekshiruv sikli (DB o'qish → qidiruv → filtr → xabar) qancha davom etdi;
- API javobida necha marta kutilmagan sxema (schema anomaly) uchradi.

Ataylab oddiy: faqat process xotirasida, restart'da nolga tushadi — uzoq
muddatli analitika emas, operativ diagnostika uchun (masalan admin /metrics
buyrug'i yoki loglar orqali ko'rish uchun). Prometheus/tashqi tizim kerak
bo'lsa, bu interfeys (snapshot() -> dict) ustiga osongina eksport qo'shish
mumkin — hozircha shu darajada minimal tutilgan.
"""

import threading
from collections import defaultdict


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        # Oxirgi N ta qiymatning sodda "oxirgisi" ko'rinishidagi gauge'lari
        # (masalan oxirgi qidiruv navbatda qancha kutgani) — to'liq
        # histogram emas, lekin diagnostika uchun yetarli.
        self._gauges: dict[str, float] = {}

    def incr(self, name: str, by: int = 1):
        with self._lock:
            self._counters[name] += by

    def set_gauge(self, name: str, value: float):
        with self._lock:
            self._gauges[name] = value

    def snapshot(self) -> dict:
        with self._lock:
            data = dict(self._counters)
            data.update({f"last_{k}": v for k, v in self._gauges.items()})
            return data

    def reset(self):
        with self._lock:
            self._counters.clear()
            self._gauges.clear()


# Butun process uchun bitta umumiy instance — railway_client.py va bot.py
# shu orqali hisoblagichlarni oshiradi.
metrics = Metrics()
