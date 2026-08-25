"""서브토픽 v2 실험 하네스 — 스냅샷 JSON 을 입력으로 서브토픽을 생성하고 리포트를 낸다.

`docs/subtopic_design_v2.md` 의 정의를 실제 분류 결과에 적용해 본다.

**DB 를 쓰지 않는다.** 입력은 `eval/extract_snapshot.py` 가 이미 뽑아둔 스냅샷 JSON 이고
출력도 파일이다. v2 는 다중 귀속이라 현재 스키마(`events.topic_id` 단일 FK)에 쓸 수조차 없고,
스키마를 바꾸기 전에 관점 품질부터 봐야 하므로 오프라인으로 돈다.

사용 예:
    python eval/subtopic_v2_harness.py --snapshot eval/results/topic-local-t002-snapshot.json \
        --tag a1 --min-events 3

기본 대상은 로컬 Ollama 다(`--base-url`, `--model`). 유료 API 로 바꾸려면 둘 다 넘기면 된다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "eval"))

from openai_client.client import LLMClient  # noqa: E402
from subtopic_v2_prompts import (  # noqa: E402
    MAX_NAME_CHARS,
    build_initial_subtopic_prompt,
    build_membership_prompt,
    build_perspective_discovery_prompt,
    build_cluster_name_prompt,
    build_event_label_prompt,
    build_pairwise_membership_prompt,
    build_perspective_refine_prompt,
    build_single_event_membership_prompt,
)

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen3:30b-instruct"
DEFAULT_SUMMARY_CHARS = 250
RESULTS_DIR = _REPO_ROOT / "eval" / "results"


def load_topics(snapshot_path: Path, min_events: int) -> tuple[list[dict], dict[int, dict]]:
    """스냅샷에서 (대상 토픽 목록, 이벤트 상세 인덱스)를 만든다.

    스냅샷은 같은 이벤트를 두 곳에 다르게 담는다 — `topics[].events[]` 는 계층 정보
    (core_content, article_ids)만, `events[]` 는 본문 정보(summary, article_count)만 갖는다.
    프롬프트에는 둘 다 필요해서 id 로 합친다.
    """
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    detail = {e["id"]: e for e in data.get("events", [])}

    targets = []
    for topic in data.get("topics", []):
        events = topic.get("events") or []
        if len(events) < min_events:
            continue
        targets.append(topic)

    targets.sort(key=lambda t: -len(t["events"]))
    return targets, detail


def format_events_block(events: list[dict], detail: dict[int, dict], summary_chars: int) -> str:
    lines = []
    for ev in events:
        info = detail.get(ev["id"], {})
        title = (ev.get("title") or info.get("title") or "").strip()
        core = (ev.get("core_content") or info.get("core_content") or "").strip()
        summary = (info.get("summary") or "").strip()
        if summary_chars > 0 and len(summary) > summary_chars:
            summary = summary[:summary_chars] + "…"

        meta = []
        if info.get("article_count"):
            meta.append(f"기사 {info['article_count']}건")
        created = (info.get("created_at") or ev.get("created_at") or "")[:10]
        if created:
            meta.append(created)

        lines.append(f"[{ev['id']}] {title}")
        if core and core != title:
            lines.append(f"    {core}")
        if summary:
            lines.append(f"    {summary}")
        if meta:
            lines.append(f"    ({', '.join(meta)})")
    return "\n".join(lines)


def ask_json(
    client,
    prompt: str,
    required_keys: set[str],
    attempts: int = 3,
    temperature: float | None = None,
) -> dict:
    """JSON 파싱 실패를 재시도한다.

    로컬 30B 는 가끔 문자열 안에 따옴표를 흘려 JSON 을 깨뜨린다(b3 에서 25건 중 2건).
    `.env` 의 LLM_MAX_RETRIES 는 HTTP 계층 재시도라 이 경우를 잡지 못한다.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            # temp 0 에서 파싱이 깨지면 재시도해도 같은 출력이 나온다(t1-c 의 90007 이
            # 3회 모두 같은 위치에서 깨졌다). 재시도부터는 온도를 살짝 올려 결정성을 깬다.
            temp = temperature
            if attempt > 0 and temperature is not None and temperature == 0:
                temp = 0.3
            extra = {} if temp is None else {"temperature": temp}
            return client.request_json(prompt, required_keys=required_keys, **extra)
        except Exception as exc:  # 파싱 실패·키 누락 모두 같은 처방(다시 묻기)이다
            last = exc
            if attempt + 1 < attempts:
                print(f"    재시도 {attempt + 1}/{attempts - 1}: {exc}", file=sys.stderr)
    raise last  # type: ignore[misc]


def discover_bottom_up(
    client,
    topic_title: str,
    per_event_blocks: list[tuple[int, str]],
    max_subtopics: int,
    temperature: float | None,
    embed_host: str,
    sim_threshold: float = 0.80,
) -> tuple[list[dict], list[str]]:
    """이벤트별 라벨 → 임베딩 클러스터 → 2건 이상만 관점으로 승격.

    top-down 자유 생성의 커버리지 구멍(t1-c 단독 48.9%)에 대한 구조 처방. 관점의 근거가
    모델의 자기 신고(based_on)가 아니라 라벨의 실제 분포에서 나온다.
    """
    from subtopic_v2_recluster import embed  # eval/ 경로에서 지연 임포트

    notes: list[str] = []
    label_events: dict[str, set[int]] = {}
    for event_id, event_text in per_event_blocks:
        try:
            answer = ask_json(
                client,
                build_event_label_prompt(topic_title, event_text),
                {"labels"},
                temperature=temperature,
            )
        except Exception as exc:
            notes.append(f"라벨 추출 실패: ({event_id}) {exc}")
            continue
        for raw in answer.get("labels") or []:
            text = str(raw).strip()
            if 1 < len(text) <= 20:
                label_events.setdefault(text, set()).add(event_id)

    texts = sorted(label_events)
    if not texts:
        return [], notes + ["라벨이 하나도 안 나왔다"]

    # 유사 라벨 병합 (union-find, 코사인 임계)
    vectors = embed(texts, embed_host, "bge-m3")
    parent = list(range(len(texts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            sim = sum(a * b for a, b in zip(vectors[i], vectors[j]))
            if sim >= sim_threshold:
                parent[find(j)] = find(i)

    clusters: dict[int, dict] = {}
    for i, text in enumerate(texts):
        root = find(i)
        c = clusters.setdefault(root, {"labels": [], "event_ids": set()})
        c["labels"].append(text)
        c["event_ids"] |= label_events[text]

    candidates = sorted(
        (c for c in clusters.values() if len(c["event_ids"]) >= 2),
        key=lambda c: -len(c["event_ids"]),
    )

    # 이벤트 집합이 거의 같은 클러스터는 이름 붙이기 전에 병합한다. t3-c 의 지방의회 토픽이
    # "의장단 및 상임위원장 구성"(7/7)·"개원 및 의장단 구성"(7/7)·"상임위원장 선출"(6/7) 로
    # 갈라졌다 — 라벨 표현만 다르고 같은 갈래다. 자카드로 판정하므로 결정적이다.
    merged: list[dict] = []
    for c in candidates:
        home = None
        for m in merged:
            union = m["event_ids"] | c["event_ids"]
            if union and len(m["event_ids"] & c["event_ids"]) / len(union) >= 0.6:
                home = m
                break
        if home is not None:
            home["labels"].extend(c["labels"])
            home["event_ids"] |= c["event_ids"]
        else:
            merged.append({"labels": list(c["labels"]), "event_ids": set(c["event_ids"])})

    # 토픽 이벤트의 70% 이상을 덮는 클러스터는 토픽 재진술이라 관점이 못 된다
    # ("트럼프 행정부의 외교 및 내정 전략" 7/10). 단, 그것뿐이면 없애지 않는다.
    n_events = len(per_event_blocks)
    narrow = [c for c in merged if len(c["event_ids"]) / n_events < 0.7]
    dropped_wide = len(merged) - len(narrow)
    if narrow:
        merged = narrow

    promoted = merged[:max_subtopics]
    notes.append(
        f"라벨 {len(texts)}개 → 클러스터 {len(clusters)}개 → 중복 병합 {len(candidates)}→"
        f"{len(merged) + dropped_wide}개 → 재진술 컷 {dropped_wide}개 → 승격 {len(promoted)}개"
    )

    title_by_id = dict(per_event_blocks)
    perspectives: list[dict] = []
    for idx, c in enumerate(promoted):
        titles = [title_by_id[i].splitlines()[0] for i in sorted(c["event_ids"]) if i in title_by_id]
        # 형제 갈래를 보여줘야 옆 갈래와 겹치는 이름을 피한다. t3-c 의 "장애인고용공단과의
        # 협업 및 고용 지원 정책" 과 "장애인공단과의 협약 동향" 은 이벤트 집합이 달라
        # 자카드 병합을 피해갔다 — 이름 단계에서만 막을 수 있다.
        sib = [f"- {p['name']}" for p in perspectives]
        sib += [
            "- (이름 미정) 라벨: " + ", ".join(sorted(set(o["labels"]))[:4])
            for o in promoted[idx + 1 :]
        ]
        try:
            named = ask_json(
                client,
                build_cluster_name_prompt(topic_title, c["labels"], titles, "\n".join(sib)),
                {"name"},
                temperature=temperature,
            )
            name = str(named.get("name") or "").strip()
        except Exception as exc:
            notes.append(f"이름 붙이기 실패({c['labels'][:2]}): {exc}")
            continue
        if not name or any(p["name"] == name for p in perspectives):
            continue
        if restates_topic(name, topic_title):
            notes.append(f"토픽 재진술이라 폐기: {name}")
            continue
        perspectives.append(
            {
                "name": name,
                "perspective": "",
                "reason": "라벨: " + ", ".join(sorted(set(c["labels"]))[:6]),
                "event_ids": [],  # 귀속은 pairwise 게이트가 결정한다
            }
        )
    return perspectives, notes


def run_two_stage(
    client,
    topic_title: str,
    events_block: str,
    event_count: int,
    per_event_blocks: list[tuple[int, str]] | None = None,
    refine: bool = False,
    temperature: float | None = None,
    existing_block: str | None = None,
    carry_mode: str = "preserve",
    pairwise: bool = False,
    perspectives_override: list[dict] | None = None,
) -> dict:
    """관점 발견 → 이벤트별 귀속 두 번의 호출로 서브토픽을 만든다.

    반환 형태를 1단계 방식(`build_initial_subtopic_prompt`)의 출력과 똑같이 맞춰서
    검증·지표 계산 코드를 공유한다.
    """
    if perspectives_override is not None:
        discovered = {"subtopics": [dict(p) for p in perspectives_override]}
    else:
        discovered = ask_json(
            client,
            build_perspective_discovery_prompt(
                topic_title, events_block, event_count, existing_block, carry_mode
            ),
            {"subtopics"},
            temperature=temperature,
        )

    perspectives = []
    rule_notes: list[str] = []
    for raw in discovered.get("subtopics") or []:
        name = str(raw.get("name") or "").strip()
        if not name or any(p["name"] == name for p in perspectives):
            continue
        # based_on 은 참고용으로만 기록한다. 하드컷은 t1-c 에서 실패했다 — 모델이 based_on 을
        # 과소 신고해서(실제 2건짜리 축에 1건만 적음) 관점이 전멸했다(90005 서브토픽 0개).
        # 근거의 판정은 자기 신고가 아니라 pairwise 귀속의 실제 결과에 맡긴다.
        based_on = raw.get("based_on")
        if isinstance(based_on, list):
            valid = {int(v) for v in based_on if str(v).lstrip("-").isdigit()}
            if len(valid) < 2:
                rule_notes.append(f"근거 신고 1건 이하(유지, 참고): {name} (based_on={sorted(valid)})")
        perspectives.append(
            {
                "name": name,
                "perspective": str(raw.get("perspective") or "").strip(),
                "reason": str(raw.get("reason") or "").strip(),
                "event_ids": [],
            }
        )
    if not perspectives:
        return {"subtopics": [], "standalone_event_ids": [], "_refine_notes": rule_notes,
                "_discovered": 0}

    merged_note: list[str] = rule_notes
    if refine and len(perspectives) > 1:
        candidate_block = "\n".join(f"- {p['name']}: {p['reason']}" for p in perspectives)
        try:
            refined = ask_json(
                client,
                build_perspective_refine_prompt(topic_title, candidate_block),
                {"final"},
                temperature=temperature,
            )
        except Exception as exc:  # 정제는 부가 단계다. 실패하면 원본 목록으로 계속한다.
            merged_note.append(f"관점 정제 실패(원본 유지): {exc}")
        else:
            by_name = {p["name"]: p for p in perspectives}
            kept: list[dict] = []
            for row in refined.get("final") or []:
                name = str(row.get("name") or "").strip()
                target = by_name.get(name)
                if target is None or target in kept:
                    continue
                absorbed = [
                    str(a).strip() for a in (row.get("absorbed") or [])
                    if str(a).strip() in by_name and str(a).strip() != name
                ]
                if absorbed:
                    merged_note.append(f"'{name}' 로 합침: {', '.join(absorbed)}")
                kept.append(target)
            if kept:
                perspectives = kept
            else:
                merged_note.append("관점 정제 결과가 비어 원본 목록을 유지했다")

    block = "\n".join(f"- {p['name']}: {p['reason']}" for p in perspectives)
    by_name = {p["name"]: p for p in perspectives}

    def attach(event_id: int, names) -> None:
        for name in names or []:
            target = by_name.get(str(name).strip())
            if target is not None and event_id not in target["event_ids"]:
                target["event_ids"].append(event_id)

    if pairwise and per_event_blocks is not None:
        # (이벤트 × 관점) 쌍마다 예/아니오 — 7차 진단의 최우선 처방. 후보를 하나만 보여주면
        # 다중 선택의 정방향 압력이 사라지고, 원문 인용을 코드로 대조해 연상 귀속을 차단한다.
        def _plain(text: str) -> str:
            return "".join(text.split())

        for event_id, event_text in per_event_blocks:
            event_plain = _plain(event_text)
            for p in perspectives:
                answer = ask_json(
                    client,
                    build_pairwise_membership_prompt(
                        topic_title, p["name"], p["reason"], event_text
                    ),
                    {"fit"},
                    temperature=temperature,
                )
                if not answer.get("fit"):
                    continue
                quote = str(answer.get("quote") or "").strip()
                if not quote or quote == "없음":
                    merged_note.append(f"근거 없는 fit=true 폐기: ({event_id}) → {p['name']}")
                    continue
                if len(_plain(quote)) >= 4 and _plain(quote) not in event_plain:
                    merged_note.append(
                        f"인용이 원문에 없어 폐기: ({event_id}) → {p['name']} [{quote[:40]}]"
                    )
                    continue
                if event_id not in p["event_ids"]:
                    p["event_ids"].append(event_id)
    elif per_event_blocks is not None:
        # 이벤트 하나씩 물어 주변 이벤트에 끌려가는 오귀속을 막는다.
        for event_id, event_text in per_event_blocks:
            answer = ask_json(
                client,
                build_single_event_membership_prompt(topic_title, block, event_text),
                {"subtopic_names"},
                temperature=temperature,
            )
            attach(event_id, answer.get("subtopic_names"))
    else:
        assigned = ask_json(
            client,
            build_membership_prompt(topic_title, block, events_block),
            {"memberships"},
            temperature=temperature,
        )
        for row in assigned.get("memberships") or []:
            try:
                event_id = int(row.get("event_id"))
            except (TypeError, ValueError):
                continue
            attach(event_id, row.get("subtopic_names"))

    return {
        "subtopics": [p for p in perspectives if p["event_ids"]],
        "standalone_event_ids": [],
        "_discovered": len(perspectives),
        "_refine_notes": merged_note,
    }


def validate(result: dict, valid_ids: set[int]) -> tuple[list[dict], list[int], list[str]]:
    """LLM 출력을 스냅샷 사실과 대조해 정리하고, 무엇을 고쳤는지 남긴다.

    단독 이벤트는 LLM 이 준 목록을 믿지 않고 **어느 서브토픽에도 안 들어간 이벤트**로
    다시 계산한다. 정의상 그게 단독 이벤트이고, 모델이 서브토픽에 넣은 이벤트를
    standalone 에도 중복해 넣는 실수가 잦기 때문이다.
    """
    issues: list[str] = []
    subtopics: list[dict] = []
    seen_names: set[str] = set()

    for raw in result.get("subtopics") or []:
        name = str(raw.get("name") or "").strip()
        if not name:
            issues.append("이름 없는 서브토픽을 버렸다")
            continue
        if len(name) > MAX_NAME_CHARS:
            issues.append(f"이름 길이 초과({len(name)}자): {name}")
        if name in seen_names:
            issues.append(f"이름 중복으로 버렸다: {name}")
            continue

        ids, dropped = [], []
        for value in raw.get("event_ids") or []:
            try:
                num = int(value)
            except (TypeError, ValueError):
                dropped.append(value)
                continue
            if num not in valid_ids:
                dropped.append(num)
            elif num not in ids:
                ids.append(num)
        if dropped:
            issues.append(f"'{name}' 에서 없는 event_id 제거: {dropped}")
        if not ids:
            issues.append(f"'{name}' 에 유효한 이벤트가 없어 버렸다")
            continue

        seen_names.add(name)
        subtopics.append(
            {
                "name": name,
                "perspective": str(raw.get("perspective") or "").strip(),
                "event_ids": ids,
                "reason": str(raw.get("reason") or "").strip(),
            }
        )

    covered = {i for s in subtopics for i in s["event_ids"]}
    standalone = sorted(valid_ids - covered)

    claimed = set()
    for value in result.get("standalone_event_ids") or []:
        try:
            claimed.add(int(value))
        except (TypeError, ValueError):
            continue
    both = sorted(claimed & covered)
    if both:
        issues.append(f"서브토픽과 단독에 동시에 넣은 이벤트(서브토픽 소속으로 처리): {both}")
    missed = sorted(set(standalone) - claimed)
    if missed:
        issues.append(f"모델이 단독으로 신고하지 않았지만 어디에도 없는 이벤트: {missed}")

    return subtopics, standalone, issues


def _bigrams(text: str) -> set[str]:
    cleaned = "".join(ch for ch in text if ch.isalnum())
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def echoes_event(name: str, titles: list[str], threshold: float = 0.34) -> bool:
    """서브토픽 이름이 특정 이벤트 제목의 축약인지 근사 판정한다."""
    a = _bigrams(name)
    if not a:
        return False
    for title in titles:
        b = _bigrams(title)
        if not b:
            continue
        overlap = len(a & b) / len(a)  # 이름이 제목에 얼마나 흡수되는지
        if overlap >= threshold:
            return True
    return False


def restates_topic(name: str, topic_title: str, threshold: float = 0.55) -> bool:
    """서브토픽 이름이 토픽 제목을 다시 쓴 것인지 근사 판정한다.

    토픽 제목의 재진술은 눌러도 목록이 그대로라 탐색에 쓸모가 없다. 프롬프트에 금지 규칙을
    넣어도 새기 때문에(t3-c 는 규칙이 있는데도 20개 중 12개가 토픽 낱말을 40% 이상 재사용)
    코드에서 한 번 더 막는다 — 이 레포의 "프롬프트를 믿지 않고 코드로 검증" 원칙과 같다.

    임계 0.55 의 근거는 t3-c 실측 분포다. 명백한 재진술 3건이 1.00 / 0.62 / 0.60 에 몰려 있고
    그 아래는 0.46 이하라 간격이 뚜렷하다. 0.46("트럼프 행정부의 외교 및 내정 전략", 커버리지
    70%)과 0.43("지방의회 의장단 및 상임위원장 구성", 100%)은 커버리지 컷이 따로 잡는다.
    """
    a = _bigrams(name)
    b = _bigrams(topic_title)
    if not a or not b:
        return False
    return len(a & b) / len(a) >= threshold  # 이름이 토픽 제목에 얼마나 흡수되는지


def alignment(left: list[dict], right: list[dict]) -> dict:
    """두 서브토픽 집합이 얼마나 같은 구조인지 잰다(이름이 아니라 이벤트 집합 기준).

    지금까지는 증분과 일괄을 지표별로 나란히 놓고 봤는데, 그것만으로는 "같은 구조에
    도달했는가"를 알 수 없다. 다중 귀속 비율이 같아도 완전히 다르게 묶였을 수 있다.

    각 서브토픽을 이벤트 집합으로 보고 반대편에서 가장 닮은 짝의 자카드를 구한 뒤 평균한다.
    한 방향만 보면 한쪽이 잘게 쪼갠 경우를 놓치므로 양방향을 각각 남긴다.
    """
    def sets(subs: list[dict]) -> list[frozenset]:
        return [frozenset(s["event_ids"]) for s in subs if s["event_ids"]]

    a, b = sets(left), sets(right)
    if not a or not b:
        return {"forward": 0.0, "backward": 0.0, "score": 0.0}

    def best(one: frozenset, pool: list[frozenset]) -> float:
        return max((len(one & other) / len(one | other) for other in pool), default=0.0)

    fwd = sum(best(x, b) for x in a) / len(a)
    bwd = sum(best(y, a) for y in b) / len(b)
    return {
        "forward": round(fwd, 3),
        "backward": round(bwd, 3),
        "score": round((fwd + bwd) / 2, 3),
    }


def measure(topic_results: list[dict]) -> dict:
    """v1 의 쌍 지표를 쓸 수 없으므로(집합 대 집합) 구조 지표로 본다."""
    events_total = sum(r["event_count"] for r in topic_results)
    subtopics = [s for r in topic_results for s in r["subtopics"]]
    standalone_total = sum(len(r["standalone_event_ids"]) for r in topic_results)

    memberships = sum(len(s["event_ids"]) for s in subtopics)
    multi_home = 0
    for r in topic_results:
        counts: dict[int, int] = {}
        for s in r["subtopics"]:
            for i in s["event_ids"]:
                counts[i] = counts.get(i, 0) + 1
        multi_home += sum(1 for c in counts.values() if c >= 2)

    # 포함관계 쌍: 한 서브토픽의 이벤트 집합이 같은 토픽 안 다른 서브토픽에 통째로 들어가는 경우.
    # 대개 상위 관점의 하위 개념을 중복 생성한 것이라 탐색 화면에서 군더더기가 된다.
    nested = 0
    for r in topic_results:
        sets = [(s["name"], frozenset(s["event_ids"])) for s in r["subtopics"]]
        for i, (_, a) in enumerate(sets):
            for j, (_, b) in enumerate(sets):
                if i != j and a < b:
                    nested += 1
                    break

    # 한 서브토픽이 토픽 이벤트의 몇 %를 담는가. 1건짜리 과분할의 반대쪽 극단이다.
    widest = 0.0
    wide_count = 0
    for r in topic_results:
        n = r["event_count"] or 1
        for sub in r["subtopics"]:
            share = len(sub["event_ids"]) / n
            widest = max(widest, share)
            if share >= 0.6:
                wide_count += 1

    echoed = 0
    for r in topic_results:
        titles = [str(v) for v in r["event_titles"].values()]
        echoed += sum(
            1
            for s in r["subtopics"]
            if len(s["event_ids"]) == 1 and echoes_event(s["name"], titles)
        )

    singles = sum(1 for s in subtopics if len(s["event_ids"]) == 1)
    return {
        "topics": len(topic_results),
        "events": events_total,
        "subtopics": len(subtopics),
        "subtopics_per_topic": round(len(subtopics) / len(topic_results), 2) if topic_results else 0.0,
        "memberships": memberships,
        # v2 의 존재 이유가 다중 귀속이다. 이 값이 1.0 에 가까우면 v1 과 다를 게 없다는 뜻이다.
        "memberships_per_event": round(memberships / events_total, 2) if events_total else 0.0,
        "multi_home_events": multi_home,
        "multi_home_ratio": round(multi_home / events_total, 3) if events_total else 0.0,
        "standalone_events": standalone_total,
        "standalone_ratio": round(standalone_total / events_total, 3) if events_total else 0.0,
        "widest_coverage": round(widest, 3),
        "wide_subtopics": wide_count,
        "wide_ratio": round(wide_count / len(subtopics), 3) if subtopics else 0.0,
        "event_echo_names": echoed,
        "event_echo_ratio": round(echoed / len(subtopics), 3) if subtopics else 0.0,
        "nested_subtopics": nested,
        "nested_ratio": round(nested / len(subtopics), 3) if subtopics else 0.0,
        "single_event_subtopics": singles,
        "single_event_subtopic_ratio": round(singles / len(subtopics), 3) if subtopics else 0.0,
    }


def render_report(payload: dict) -> str:
    m = payload["metrics"]
    out = [
        f"# 서브토픽 v2 실험: {payload['tag']}",
        "",
        f"- 스냅샷: `{payload['snapshot']}`",
        f"- 모델: `{payload['model']}` @ `{payload['base_url']}`",
        f"- 실행: {payload['timestamp']} ({payload['elapsed_seconds']}초)",
        f"- 대상 기준: 이벤트 {payload['min_events']}개 이상 토픽 / 방식: `{payload.get('mode', 'single')}`"
        + (f" / 귀속: `{payload['membership']}`" if payload.get('mode') == 'two-stage' else '')
        + (' / 관점 정제 켬' if payload.get('refine') else '')
        + f" / temp {payload.get('temperature', '?')}"
        + (" / 이전 구조 이어받음" if payload.get('carry_over') else ''),
        # 실패 토픽은 measure() 에서 빠지므로 표본이 조용히 줄어든다. t2-c 는 90007 이 JSON
        # 파싱으로 죽어 5토픽 중 4토픽(47/54 이벤트)만 집계됐는데 리포트에 그 사실이 없어,
        # 다른 실행과 그대로 비교하면 잘못된 결론이 나온다. 표본이 줄었으면 반드시 적는다.
        *(
            [f"- ⚠️ 실패로 지표에서 빠진 토픽 {failed}개"
             f" (전체 {len(payload['topics'])}개 중 {len(payload['topics']) - failed}개만 집계)"]
            if (failed := sum(1 for t in payload["topics"] if t.get("error")))
            else []
        ),
        "",
        "## 구조 지표",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 대상 토픽 / 이벤트 | {m['topics']} / {m['events']} |",
        f"| 생성된 서브토픽 | {m['subtopics']} (토픽당 {m['subtopics_per_topic']}) |",
        f"| 귀속 관계 수 | {m['memberships']} (이벤트당 {m['memberships_per_event']}) |",
        f"| 2개 이상 서브토픽에 속한 이벤트 | {m['multi_home_events']} ({m['multi_home_ratio']:.1%}) |",
        f"| 단독 이벤트 | {m['standalone_events']} ({m['standalone_ratio']:.1%}) |",
        f"| 이벤트 1개짜리 서브토픽 | {m['single_event_subtopics']} ({m['single_event_subtopic_ratio']:.1%}) |",
        f"| 다른 서브토픽에 통째로 포함된 것 | {m['nested_subtopics']} ({m['nested_ratio']:.1%}) |",
        f"| 이벤트 하나짜리 + 제목 축약 이름 | {m['event_echo_names']} ({m['event_echo_ratio']:.1%}) |",
        f"| 토픽의 60% 이상을 담는 서브토픽 | {m['wide_subtopics']} ({m['wide_ratio']:.1%}) · 최대 {m['widest_coverage']:.0%} |",
        "",
        "> `이벤트당 귀속 수` 가 1.0 근처면 다중 귀속이 안 일어난 것이라 v1 단일 귀속과 다를 게 없다.",
        "",
        "## 토픽별 결과",
    ]

    for r in payload["topics"]:
        out += ["", f"### [{r['topic_id']}] {r['topic_title']} — 이벤트 {r['event_count']}개", ""]
        if r.get("error"):
            out.append(f"**실패**: {r['error']}")
            continue
        for s in r["subtopics"]:
            tag = f" · {s['perspective']}" if s["perspective"] else ""
            out.append(f"- **{s['name']}**{tag} — {len(s['event_ids'])}건")
            for i in s["event_ids"]:
                out.append(f"  - ({i}) {r['event_titles'].get(str(i), r['event_titles'].get(i, ''))}")
            if s["reason"]:
                out.append(f"  - _근거: {s['reason']}_")
        if r["standalone_event_ids"]:
            out.append(f"- **단독 이벤트** — {len(r['standalone_event_ids'])}건")
            for i in r["standalone_event_ids"]:
                out.append(f"  - ({i}) {r['event_titles'].get(str(i), r['event_titles'].get(i, ''))}")
        if r["issues"]:
            out.append("")
            out.append("검증에서 고친 것:")
            for issue in r["issues"]:
                out.append(f"  - {issue}")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="서브토픽 v2 실험 하네스")
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--tag", required=True, help="결과 파일 이름에 쓰는 실행 식별자")
    parser.add_argument("--min-events", type=int, default=3, help="대상 토픽의 최소 이벤트 수(실험 3, 서비스 7)")
    parser.add_argument("--topic-id", type=int, action="append", help="특정 토픽만 (반복 지정)")
    parser.add_argument("--limit", type=int, help="대상 토픽 수 상한(이벤트 많은 순)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default="ollama")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--carry-over", type=Path, metavar="RESULT_JSON",
                        help="이전 실행 결과를 이어받아 서브토픽 이름을 유지한다(재생성 안정성)")
    parser.add_argument("--temperature", type=float, default=0.0,
                        help="샘플링 온도. 기본 0 — 온도를 안 주면 같은 설정 재실행에서도 구조가 절반만 겹친다")
    parser.add_argument("--mode", choices=["two-stage", "single"], default="two-stage",
                        help="two-stage=관점 발견 후 이벤트별 귀속(기본), single=한 번의 호출")
    parser.add_argument("--refine", action="store_true",
                        help="관점 발견 뒤 실질적으로 같은 관점을 합치는 단계를 넣는다")
    parser.add_argument("--discovery", choices=["llm", "bottomup"], default="llm",
                        help="관점 발견 방식. bottomup=이벤트별 라벨→클러스터→2건 이상 승격")
    parser.add_argument("--sim-threshold", type=float, default=0.80,
                        help="bottomup 라벨 병합 코사인 임계")
    parser.add_argument("--membership", choices=["batch", "per-event", "pairwise"], default="batch",
                        help="two-stage 의 2단계를 이벤트 전체 한 번에(batch) 또는 하나씩(per-event) 판정")
    parser.add_argument("--summary-chars", type=int, default=DEFAULT_SUMMARY_CHARS,
                        help="이벤트 요약을 몇 자까지 프롬프트에 넣을지 (0이면 제외)")
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args()

    targets, detail = load_topics(args.snapshot, args.min_events)
    if args.topic_id:
        wanted = set(args.topic_id)
        targets = [t for t in targets if t["id"] in wanted]
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        print("대상 토픽이 없다. --min-events 를 낮추거나 스냅샷을 확인할 것.", file=sys.stderr)
        return 1

    client = LLMClient(
        model=args.model, api_key=args.api_key, base_url=args.base_url, timeout=args.timeout
    )
    print(f"대상 토픽 {len(targets)}개 / 모델 {args.model}", file=sys.stderr)

    carried: dict[int, list[str]] = {}
    if args.carry_over and args.carry_over.exists():
        prior = json.loads(args.carry_over.read_text(encoding="utf-8"))
        for r in prior.get("topics", []):
            if not r.get("error"):
                carried[r["topic_id"]] = [
                    f"- {s['name']}: {s.get('reason', '')}" for s in r["subtopics"]
                ]
        print(f"이어받기: 토픽 {len(carried)}개의 기존 서브토픽", file=sys.stderr)

    started = time.time()
    topic_results = []
    for idx, topic in enumerate(targets, 1):
        events = topic["events"]
        valid_ids = {e["id"] for e in events}
        block = format_events_block(events, detail, args.summary_chars)
        prompt = build_initial_subtopic_prompt(topic["title"], block, len(events))

        entry = {
            "topic_id": topic["id"],
            "topic_title": topic["title"],
            "event_count": len(events),
            "event_titles": {e["id"]: (e.get("title") or "") for e in events},
            "subtopics": [],
            "standalone_event_ids": [],
            "issues": [],
        }
        t0 = time.time()
        try:
            if args.mode == "two-stage":
                per_event = None
                if args.membership in ("per-event", "pairwise") or args.discovery == "bottomup":
                    per_event = [
                        (e["id"], format_events_block([e], detail, args.summary_chars))
                        for e in events
                    ]

                override = None
                bottomup_notes: list[str] = []
                if args.discovery == "bottomup":
                    embed_host = args.base_url.rsplit("/v1", 1)[0]
                    override, bottomup_notes = discover_bottom_up(
                        client, topic["title"], per_event,
                        max(2, min(8, (len(events) + 2) // 2)),
                        args.temperature, embed_host, args.sim_threshold,
                    )
                prior_block = "\n".join(carried.get(topic["id"], [])) or None
                raw = run_two_stage(
                    client, topic["title"], block, len(events), per_event, args.refine,
                    args.temperature, prior_block,
                    pairwise=(args.membership == "pairwise"),
                    perspectives_override=override,
                )
                raw["_refine_notes"] = bottomup_notes + list(raw.get("_refine_notes") or [])
            else:
                raw = ask_json(client, prompt, {"subtopics"}, temperature=args.temperature)
        except Exception as exc:  # 한 토픽이 실패해도 나머지는 계속 본다
            entry["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  [{idx}/{len(targets)}] {topic['title'][:30]} 실패: {exc}", file=sys.stderr)
        else:
            subtopics, standalone, issues = validate(raw, valid_ids)
            issues = list(raw.get("_refine_notes") or []) + issues
            entry.update(
                subtopics=subtopics, standalone_event_ids=standalone, issues=issues, raw=raw
            )
            print(
                f"  [{idx}/{len(targets)}] {topic['title'][:30]} "
                f"→ 서브토픽 {len(subtopics)} / 단독 {len(standalone)} ({time.time()-t0:.0f}s)",
                file=sys.stderr,
            )
        topic_results.append(entry)

    payload = {
        "tag": args.tag,
        "snapshot": str(args.snapshot),
        "model": args.model,
        "base_url": args.base_url,
        "min_events": args.min_events,
        "mode": args.mode,
        "membership": args.membership,
        "discovery": args.discovery,
        "refine": args.refine,
        "temperature": args.temperature,
        "carry_over": str(args.carry_over) if args.carry_over else None,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.time() - started),
        "metrics": measure([r for r in topic_results if not r.get("error")]),
        "topics": topic_results,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / f"subtopic-v2-{args.tag}.json"
    md_path = args.out_dir / f"subtopic-v2-{args.tag}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_report(payload), encoding="utf-8")
    print(f"\n저장: {json_path}\n      {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
