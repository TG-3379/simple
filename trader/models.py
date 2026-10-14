from dataclasses import dataclass, field


@dataclass
class Candidate:
    code: str
    name: str = ""
    price: float = 0.0
    change_rate: float = 0.0  # 전일대비 등락률(%)
    open_price: float = 0.0


@dataclass
class Position:
    code: str
    name: str = ""
    qty: int = 0
    avg: float = 0.0
    buys_done: int = 0          # 분할매수 진행 회차
    last_buy_price: float = 0.0
    base_qty: int = 0           # 분할매도 비율 기준 수량(최대 보유량)
    sold_stages: set = field(default_factory=set)

    def add(self, qty: int, price: float) -> None:
        total = self.avg * self.qty + price * qty
        self.qty += qty
        self.avg = total / self.qty
        self.last_buy_price = price
        self.base_qty = max(self.base_qty, self.qty)
