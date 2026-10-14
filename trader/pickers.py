"""종목선택 - 조건검색식(ka10172) / 관심종목 / 실시간 조회 순위(ka10027)."""
import logging

from models import Candidate

log = logging.getLogger("pickers")


def _num(v) -> float:
    try:
        return float(str(v).replace("+", "").replace(",", ""))
    except ValueError:
        return 0.0


def by_condition(seq) -> list[Candidate]:
    """조건검색식 (ka10172, WebSocket). seq=조건식 일련번호(ka10171 목록조회로 확인)."""
    import asyncio

    from kiwoom import get_ws_client

    async def run():
        body = {"trnm": "CNSRREQ", "seq": str(seq), "search_type": "0",
                "stex_tp": "K", "cont_yn": "N", "next_key": ""}
        return await get_ws_client().request_once(api_url="/api/dostk/websocket", body=body)

    resp = asyncio.run(run())
    return [
        Candidate(code=r["9001"].lstrip("A"), name=r.get("302", ""),
                  price=abs(_num(r.get("10"))), change_rate=_num(r.get("12")),
                  open_price=abs(_num(r.get("16"))))
        for r in resp.get("data", [])
    ]


def by_watchlist(group: str) -> list[Candidate]:
    """관심종목: 키움 관심종목 그룹(ka01300 의 그룹코드)의 종목을 ka01301 로 가져오고, ka10095 로 시세를 채움."""
    from kiwoom import get_client

    client = get_client()
    detail = client.fetch_page(
        api_id="ka01301", path="/api/dostk/watchlist", body={"arn_grp_id": str(group)},
        cont_yn=None, next_key=None,
    ).body
    # 'BLANK' 는 HTS 관심종목의 빈 구분 행이라 제외, 중복도 제거
    codes = list(dict.fromkeys(r["cod2"] for r in detail.get("nofj", []) if r["cod2"] != "BLANK"))
    out = []
    for i in range(0, len(codes), 50):  # 한 번에 여러 종목을 | 로 이어 조회
        info = client.fetch_page(
            api_id="ka10095", path="/api/dostk/stkinfo", body={"stk_cd": "|".join(codes[i:i + 50])},
            cont_yn=None, next_key=None,
        ).body
        out += [
            Candidate(code=r["stk_cd"], name=r["stk_nm"], price=abs(_num(r["cur_prc"])),
                      change_rate=_num(r["flu_rt"]), open_price=abs(_num(r["open_pric"])))
            for r in info.get("atn_stk_infr", [])
        ]
    return out


def by_ranking(p: dict, sort_tp: str) -> list[Candidate]:
    """전일대비등락률상위 (ka10027). sort_tp 1:상승률 3:하락률."""
    from kiwoom import get_client

    resp = get_client().fetch_page(
        api_id="ka10027", path="/api/dostk/rkinfo",
        body={"mrkt_tp": p.get("시장", "000"), "sort_tp": sort_tp, "trde_qty_cnd": "0000",
              "stk_cnd": "0", "crd_cnd": "0", "updown_incls": "0", "pric_cnd": "0",
              "trde_prica_cnd": "0", "stex_tp": "1"},
        cont_yn=None, next_key=None,
    ).body
    return [
        Candidate(code=r["stk_cd"], name=r["stk_nm"], price=abs(_num(r["cur_prc"])),
                  change_rate=_num(r["flu_rt"]))
        for r in resp.get("pred_pre_flu_rt_upper", [])
    ]


def pick(name: str, value, p: dict, strategy_name: str) -> list[Candidate]:
    """value 의 의미는 방식마다 다르다: 조건검색식=조건식 번호, 관심종목=관심종목 그룹코드, 순위=상위 N개."""
    if name == "조건검색식":
        return by_condition(value)
    if name == "관심종목":
        return by_watchlist(value)
    if name == "실시간 조회 순위":
        # 하락 전략이면 하락률 순위, 아니면 상승률 순위에서 상위 N개를 후보로 가져온다.
        return by_ranking(p, "3" if "하락" in strategy_name else "1")[: int(value)]
    raise ValueError(f"알 수 없는 종목선택: {name}")
