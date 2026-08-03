"""토픽 cannot-link 정답을 만든다 — 지금 0쌍이라 토픽 과병합을 전혀 못 재고 있다.

`eval/review_worklist.py` 는 이벤트 전용이라 토픽에는 쓸 수 없다. 그래서 채택 설정(t003,
서브토픽 임계 0.50)의 스냅샷에서 이벤트가 2개 이상인 토픽 7개를 열어 직접 판정했다.
사람 검수가 아니라 Claude 가 이벤트 제목을 읽고 내린 **파생 판정**이다.

판정 방식: 한 토픽 안에서 실제로 다른 주제인 것들을 그룹으로 가른다. 그룹이 둘 이상이면
그 토픽은 과병합이고, 서로 다른 그룹의 기사 쌍이 cannot-link 가 된다.

쌍이 폭증하지 않도록 이벤트마다 기사 3건(id 오름차순)만 대표로 쓴다. 전수로 만들면
T1632 하나에서만 수백 쌍이 나와 특정 토픽이 지표를 지배한다.
"""

import json
from itertools import combinations

ROOT = "/Users/minjae/Desktop/Hannoon-Fork/hannoon-ai"
SNAP = f"{ROOT}/eval/results/topic-t003-snapshot.json"
GOLD = f"{ROOT}/eval/data/constraints/review_2026-08-03-derived.json"
SAMPLE_PER_EVENT = 3

# 토픽 id -> (그룹 목록, 판정 근거). 그룹 하나 = 하나의 주제.
DECISIONS = {
    1633: ([[1626], [1636, 1659], [1655], [1691]],
           "검찰 보완수사권 / 선관위 특검·감사 / 정보통신망법 시행 / 한동훈 징계는 각각 다른 사안. "
           "'개혁 논의'라는 추상 수준에서만 겹친다."),
    1623: ([[1607, 1690], [1634], [1693]],
           "코스피 반도체 급락과 삼성전자 실적은 같은 반도체 시황이지만, 레버리지 ETF 유동성 규제와 "
           "SK하이닉스 나스닥 상장은 별개 사안."),
    1632: ([[1625], [1629], [1656], [1675]],
           "건국 250주년 기념행사 / 네타냐후 정상회담 / FIFA 회장 통화 / 나토 무기 세일즈 — "
           "공통점이 '트럼프'뿐인 전형적 인물 중심 과병합."),
    1626: ([[1616, 1648], [1668]],
           "청룡기 조롱 논란과 그에 대한 조국 발언은 한 사안이지만, 이병태 부위원장 자진사퇴는 "
           "다른 사건(이벤트 검수에서도 분리했다)."),
    # 아래 셋은 과병합으로 보지 않는다 — 그룹을 나누지 않으므로 cannot 이 생기지 않는다.
    1621: ([[1588, 1684]], "홈플러스 회생절차와 카드대금 지급보류는 한 사태의 국면들."),
    1634: ([[1627, 1700]], "둘 다 민주당 당권 경쟁 국면."),
    1635: ([[1646, 1650]], "미래대응기금과 3대 메가 프로젝트는 같은 정책 패키지."),
}


def main() -> None:
    snap = json.load(open(SNAP, encoding="utf-8"))
    ev_articles = {}
    for t in snap["topics"]:
        for ev in t.get("events") or []:
            ids = sorted(ev.get("article_ids") or [])
            if ids:
                ev_articles[ev["id"]] = ids[:SAMPLE_PER_EVENT]

    pairs, log = [], []
    for topic_id, (groups, reason) in DECISIONS.items():
        if len(groups) < 2:
            continue
        for ga, gb in combinations(groups, 2):
            a_ids = [x for e in ga for x in ev_articles.get(e, [])]
            b_ids = [x for e in gb for x in ev_articles.get(e, [])]
            for x in a_ids:
                for y in b_ids:
                    pairs.append(sorted((x, y)))
        log.append({"unit": "topic", "ref_id": topic_id, "label": "split",
                    "memo": f"[claude 파생판정] {reason}"})

    # 중복 제거 후 결정적 순서로
    uniq = sorted({tuple(p) for p in pairs})
    print(f"토픽 cannot-link {len(uniq)}쌍 생성 (과병합 판정 토픽 {len(log)}개)")

    gold = json.load(open(GOLD, encoding="utf-8"))
    tc = gold.setdefault("topic_constraints", {})
    tc.setdefault("must_link", [])
    before = len(tc.get("cannot_link") or [])
    existing = {tuple(sorted(p[:2])) for p in (tc.get("cannot_link") or [])}
    tc["cannot_link"] = [list(p) for p in sorted(existing | set(uniq))]

    # must 와 충돌하면 안 된다 — 이벤트 때 겪은 모순을 미리 막는다
    must = {tuple(sorted(p[:2])) for p in (tc.get("must_link") or [])}
    conflict = must & set(tc["cannot_link"] and {tuple(p) for p in tc["cannot_link"]})
    if conflict:
        raise SystemExit(f"모순: must 와 cannot 에 동시에 든 쌍 {len(conflict)}개 (예 {sorted(conflict)[:3]})")

    gold.setdefault("review_log", []).extend(log)
    gold["derived_note"] = (gold.get("derived_note", "") +
                            " 토픽 cannot-link 는 2026-08-03 t003 스냅샷을 보고 Claude 가 판정한 파생분이다.")
    json.dump(gold, open(GOLD, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"  토픽 cannot: {before} → {len(tc['cannot_link'])}쌍")
    print(f"  토픽 must  : {len(tc.get('must_link') or [])}쌍 (변경 없음)")


if __name__ == "__main__":
    main()
