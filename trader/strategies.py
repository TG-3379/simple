"""전략 - 후보 종목을 걸러내는 필터. Candidate와 전략 설정을 받아 True/False 반환."""
import logging

from models import Candidate

log = logging.getLogger("strategies")


def open_breakout(c: Candidate, p: dict) -> bool:
    """시가 돌파: 현재가가 시가 대비 N% 이상 위."""
    if not c.open_price:
        return False
    return (c.price / c.open_price - 1) * 100 > p.get("시가대비_이상", 0.0)


def m_band(c: Candidate, p: dict) -> bool:
    """M띠: 수식 미정. 수식이 정해질 때까지 항상 False."""
    if p.get("수식") is None:
        log.warning("M띠 수식 미정 -> 통과 종목 없음")
    return False


def rise_5pct(c: Candidate, p: dict) -> bool:
    return c.change_rate >= p.get("등락률_이상", 5)


def fall_5pct(c: Candidate, p: dict) -> bool:
    return c.change_rate <= p.get("등락률_이하", -5)


STRATEGIES = {
    "시가 돌파": open_breakout,
    "M띠": m_band,
    "5% 이상 상승": rise_5pct,
    "5% 하락": fall_5pct,
}
