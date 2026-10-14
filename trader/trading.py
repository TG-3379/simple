"""매매 4종: 일괄매수 / 분할매수 / 스탑로스 / 분할매도. Trader가 포지션과 한도를 관리한다."""
import logging

import holdings
import orders
from models import Candidate, Position

log = logging.getLogger("trading")


class Trader:
    def __init__(self, common: dict, trade_cfg: dict, buy_mode: str, sell_modes: list[str],
                 held_fn=None):
        """held_fn: 계좌에 실제 보유 중인 종목코드 집합을 돌려주는 함수 (기본: holdings.held_codes)."""
        self.max_stocks = common.get("최대종목수")
        if not isinstance(self.max_stocks, int) or isinstance(self.max_stocks, bool) or self.max_stocks < 1:
            raise SystemExit("공통.최대종목수는 1 이상의 정수로 반드시 지정해야 함 (null/미지정 불가)")
        self.held_fn = held_fn or (lambda: holdings.held_codes(common.get("거래소", "KRX")))
        self.order_amt = common.get("종목당주문금액")
        self.loss_limit = common.get("하루손실한도")
        self.max_trades = common.get("하루매매횟수한도")
        for key in ("종목당주문금액", "하루손실한도", "하루매매횟수한도"):
            if common.get(key) is None:
                log.warning("공통.%s 미설정(null) -> 제한 없음으로 동작", key)
        self.cfg = trade_cfg
        self.buy_mode = buy_mode
        self.split_n = 1
        if buy_mode == "분할매수":  # 균등 분할: 종목당주문금액을 분할횟수로 똑같이 나눠 매수
            n = trade_cfg["분할매수"].get("분할횟수")
            if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                raise SystemExit("매수.분할매수.분할횟수는 1 이상의 정수여야 함")
            self.split_n = n
        self.sell_modes = sell_modes
        if "분할매도" in sell_modes:
            self._check_split_sell(trade_cfg["분할매도"])
        self.positions: dict[str, Position] = {}
        self.realized = 0.0
        self.trades = 0

    @staticmethod
    def _check_split_sell(c: dict) -> None:
        step, ratio, full = (c.get(k) for k in ("수익률간격", "단계당_매도비율", "전량매도_수익률"))
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
        if not (num(step) and step > 0):
            raise SystemExit("매도.분할매도.수익률간격은 0보다 큰 숫자여야 함")
        if not (num(ratio) and 0 < ratio <= 1):
            raise SystemExit("매도.분할매도.단계당_매도비율은 0 초과 1 이하여야 함")
        if not (num(full) and full > step):
            raise SystemExit("매도.분할매도.전량매도_수익률은 수익률간격보다 커야 함")
        if c.get("매도기준") not in ("현재보유량", "최대보유량"):
            raise SystemExit('매도.분할매도.매도기준은 "현재보유량" 또는 "최대보유량"이어야 함')

    # ---- 한도 ----
    def _blocked(self, why_prefix: str = "") -> str | None:
        if self.loss_limit is not None and self.realized <= -self.loss_limit:
            return "하루손실한도 도달"
        if self.max_trades is not None and self.trades >= self.max_trades:
            return "하루매매횟수한도 도달"
        return None

    def _qty_for(self, price: float, ratio: float = 1.0, amount: float | None = None) -> int:
        """amount(원)을 ratio 만큼 쓸 때의 주문 수량. amount 가 없으면 공통.종목당주문금액."""
        if not price:
            return 0
        amount = self.order_amt if amount is None else amount
        if not amount:
            return 1
        return int(amount * ratio // price)

    def _may_open_new(self, code: str) -> bool:
        """새 종목을 사도 계좌 보유 종목 수가 최대종목수를 넘지 않는지 확인.
        계좌에서 실제 보유를 조회하고(수동 매수분 포함), 이 프로그램이 낸 주문도 합산한다.
        조회에 실패하면 안전하게 False."""
        try:
            held = set(self.held_fn())
        except Exception as e:  # 확인할 수 없으면 매수하지 않는다
            log.error("  보유종목 조회 실패 -> %s 매수 중단: %s", code, e)
            return False
        held |= {p.code for p in self.positions.values() if p.qty > 0}
        if code in held:
            log.info("  이미 보유 중인 종목 -> %s 신규 매수 건너뜀", code)
            return False
        if len(held) >= self.max_stocks:
            log.info("  최대종목수 %d 도달(계좌 보유 %d종목) -> %s 매수 건너뜀",
                     self.max_stocks, len(held), code)
            return False
        return True

    def _buy(self, pos: Position, qty: int, price: float) -> bool:
        """모든 매수 주문의 단일 통로. 새 종목이면 최대종목수를 여기서 반드시 확인한다."""
        if qty < 1:
            return False
        if pos.qty == 0 and not self._may_open_new(pos.code):
            return False
        orders.buy(pos.code, qty)
        pos.add(qty, price)
        self.trades += 1
        return True

    def _sell(self, pos: Position, qty: int, price: float, reason: str) -> None:
        qty = min(qty, pos.qty)
        if qty < 1:
            return
        orders.sell(pos.code, qty)
        self.realized += (price - pos.avg) * qty
        pos.qty -= qty
        self.trades += 1
        log.info("  매도[%s] %s %d주 @%.0f (평균 %.0f, 누적실현 %+.0f)",
                 reason, pos.code, qty, price, pos.avg, self.realized)

    # ---- 매수 ----
    def enter(self, c: Candidate) -> None:
        """후보 종목 신규 진입. 일괄매수는 전량, 분할매수는 1회차만 매수한다."""
        if c.code in self.positions and self.positions[c.code].qty > 0:
            return
        if (why := self._blocked()):
            log.info("  %s -> %s 매수 건너뜀", why, c.code)
            return
        pos = self.positions.setdefault(c.code, Position(c.code, c.name))
        if self.buy_mode == "일괄매수":
            amount = self.cfg["일괄매수"].get("주문금액")
            bought = self._buy(pos, self._qty_for(c.price, amount=amount), c.price)
        else:  # 분할매수
            bought = self._buy(pos, self._qty_for(c.price, 1 / self.split_n), c.price)  # 균등 분할 1회차
            if bought:
                pos.buys_done = 1
        if bought:
            log.info("  매수[%s] %s(%s) %d주 @%.0f", self.buy_mode, c.name, c.code, pos.qty, c.price)

    # ---- 감시 (매 주기 호출) ----
    def watch(self, pos: Position, price: float) -> None:
        if pos.qty <= 0 or not price:
            return
        if self.buy_mode == "분할매수":
            self._next_tranche(pos, price)
        gain = (price / pos.avg - 1) * 100
        if "스탑로스" in self.sell_modes:
            if gain <= self.cfg["스탑로스"]["손절률"]:
                self._sell(pos, pos.qty, price, f"스탑로스 {gain:+.1f}%")
                return
        if "분할매도" in self.sell_modes:
            self._split_sell(pos, price, gain)

    def _next_tranche(self, pos: Position, price: float) -> None:
        cfg = self.cfg["분할매수"]
        if pos.buys_done >= self.split_n or self._blocked():
            return
        if price <= pos.last_buy_price * (1 - cfg["추가매수_하락률"] / 100):
            bought = self._buy(pos, self._qty_for(price, 1 / self.split_n), price)
            pos.buys_done += 1
            if bought:
                log.info("  추가매수[%d/%d] %s @%.0f 평균 %.0f", pos.buys_done, self.split_n,
                         pos.code, price, pos.avg)

    def _split_sell(self, pos: Position, price: float, gain: float) -> None:
        """수익률이 수익률간격(%)만큼 오를 때마다(i단계 = i*간격) 보유수량의 단계당_매도비율 만큼 매도.
        전량매도_수익률 에 닿으면 남은 수량을 전부 일괄매도. 각 단계는 한 번씩만."""
        cfg = self.cfg["분할매도"]
        step, full = cfg["수익률간격"], cfg["전량매도_수익률"]
        if gain >= full:
            self._sell(pos, pos.qty, price, f"일괄매도 {gain:+.1f}%")
            return
        reached = int(gain / step + 1e-9)  # 도달한 단계 수
        for i in range(1, reached + 1):
            if i in pos.sold_stages or i * step >= full - 1e-9:
                continue
            base = pos.qty if cfg["매도기준"] == "현재보유량" else pos.base_qty
            self._sell(pos, max(1, int(base * cfg["단계당_매도비율"])), price,
                       f"분할매도 +{i * step:g}% 단계 ({gain:+.1f}%)")
            pos.sold_stages.add(i)
            if pos.qty <= 0:
                return
