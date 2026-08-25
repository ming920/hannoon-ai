"""서브토픽 v2 증분 경로 검증 — 이벤트가 하나씩 들어올 때를 시뮬레이션한다.

설계 문서 5.2 의 절차를 실제로 돌려 본다. 일괄 생성(`subtopic_v2_harness.py`)만 검증하면
서비스에서 실제로 일어나는 일 — 토픽이 임계치에 닿아 서브토픽이 생긴 뒤, 새 이벤트가 계속
들어오는 상황 — 은 한 번도 확인되지 않은 채로 남는다.

무엇을 보는가
-------------
**증분으로 쌓은 구조가 일괄 생성과 비슷한가.** 같은 이벤트 집합인데 투입 순서만 다르므로,
결과가 크게 갈리면 증분 경로에 결함이 있다는 뜻이다. 특히 3단계(새 관점 형성 판단)를
2단계(기존 귀속)와 독립적으로 수행하지 못하면, 이미 어딘가 귀속된 이벤트 때문에 새 관점이
영영 생기지 않아 서브토픽이 초기 상태에 굳는다.

절차
----
1. 토픽의 이벤트를 시간순으로 정렬한다.
2. 앞의 seed 개로 최초 서브토픽을 만든다(일괄 생성과 같은 2단계 방식).
3. 나머지를 하나씩 신규 이벤트로 투입하고, 매번 기존 귀속 + 새 관점 판단을 시킨다.
4. 최종 구조를 일괄 생성 결과와 나란히 놓는다.

사용 예:
    python eval/subtopic_v2_incremental.py --snapshot eval/results/reclustered-b2.json \\
        --tag i1 --seed 3 --compare eval/results/subtopic-v2-b5.json
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
from subtopic_v2_harness import (  # noqa: E402
    alignment,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_SUMMARY_CHARS,
    RESULTS_DIR,
    ask_json,
    format_events_block,
    load_topics,
    measure,
    run_two_stage,
    validate,
)
from subtopic_v2_prompts import (  # noqa: E402
    build_incremental_subtopic_prompt,
    build_new_perspective_prompt,
    build_single_event_membership_prompt,
)


def order_events(events: list[dict], detail: dict[int, dict]) -> list[dict]:
    """시간순 정렬. created_at 은 분류 실행 시각이라 실제 보도 순서와 완전히 같지는 않지만,
    증분 시뮬레이션에는 '어떤 순서로든 하나씩 들어온다'는 조건만 있으면 된다."""
    def key(e: dict):
        info = detail.get(e["id"], {})
        return (info.get("created_at") or e.get("created_at") or "", e["id"])

    return sorted(events, key=key)


def apply_increment(state: list[dict], answer: dict, new_id: int, valid_ids: set[int]) -> list[str]:
    """증분 응답을 현재 서브토픽 상태에 반영하고, 무엇을 걸렀는지 남긴다."""
    notes: list[str] = []
    by_name = {s["name"]: s for s in state}

    for row in answer.get("assignments") or []:
        target = by_name.get(str(row.get("subtopic_name") or "").strip())
        if target is None:
            notes.append(f"없는 서브토픽에 배정 시도: {row.get('subtopic_name')}")
            continue
        for value in row.get("add_event_ids") or []:
            try:
                num = int(value)
            except (TypeError, ValueError):
                continue
            if num in valid_ids and num not in target["event_ids"]:
                target["event_ids"].append(num)

    for raw in answer.get("new_subtopics") or []:
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        if name in by_name:
            notes.append(f"기존과 같은 이름으로 새로 만들려 함(병합): {name}")
            target = by_name[name]
        else:
            target = {
                "name": name,
                "perspective": str(raw.get("perspective") or "").strip(),
                "reason": str(raw.get("reason") or "").strip(),
                "event_ids": [],
                "created_at_event": new_id,
            }
            state.append(target)
            by_name[name] = target
        for value in raw.get("event_ids") or []:
            try:
                num = int(value)
            except (TypeError, ValueError):
                continue
            if num in valid_ids and num not in target["event_ids"]:
                target["event_ids"].append(num)

    return notes


def main() -> int:
    ap = argparse.ArgumentParser(description="서브토픽 v2 증분 경로 검증")
    ap.add_argument("--snapshot", required=True, type=Path)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--seed", type=int, default=3, help="최초 생성에 쓸 이벤트 수(실험 기준 3)")
    ap.add_argument("--min-events", type=int, default=5,
                    help="대상 토픽 최소 이벤트 수. seed 보다 커야 증분 단계가 생긴다")
    ap.add_argument("--limit", type=int, help="대상 토픽 수 상한(이벤트 많은 순)")
    ap.add_argument("--compare", type=Path, help="비교할 일괄 생성 결과 JSON")
    ap.add_argument("--split-steps", action="store_true",
                    help="증분 한 걸음을 귀속/새 관점 두 호출로 나눈다(i1 의 다중 귀속 저하 대책)")
    ap.add_argument("--recompact", type=int, default=0, metavar="N",
                    help="증분 N 걸음마다 토픽 전체를 일괄 재생성해 누적된 관점을 정리한다(0=끔)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--api-key", default="ollama")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--summary-chars", type=int, default=DEFAULT_SUMMARY_CHARS)
    ap.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = ap.parse_args()

    if args.min_events <= args.seed:
        print(f"--min-events({args.min_events})는 --seed({args.seed})보다 커야 한다.", file=sys.stderr)
        return 1

    targets, detail = load_topics(args.snapshot, args.min_events)
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        print("대상 토픽이 없다.", file=sys.stderr)
        return 1

    client = LLMClient(
        model=args.model, api_key=args.api_key, base_url=args.base_url, timeout=args.timeout
    )
    print(f"대상 토픽 {len(targets)}개 / seed {args.seed}개로 시작", file=sys.stderr)

    started = time.time()
    results = []
    for idx, topic in enumerate(targets, 1):
        ordered = order_events(topic["events"], detail)
        seed_events, rest = ordered[: args.seed], ordered[args.seed :]
        valid_ids = {e["id"] for e in ordered}
        entry = {
            "topic_id": topic["id"],
            "topic_title": topic["title"],
            "event_count": len(ordered),
            "event_titles": {e["id"]: (e.get("title") or "") for e in ordered},
            "seed_size": len(seed_events),
            "steps": [],
            "issues": [],
            "subtopics": [],
            "standalone_event_ids": [],
        }
        t0 = time.time()
        try:
            seed_block = format_events_block(seed_events, detail, args.summary_chars)
            initial = run_two_stage(
                client,
                topic["title"],
                seed_block,
                len(seed_events),
                [
                    (e["id"], format_events_block([e], detail, args.summary_chars))
                    for e in seed_events
                ],
                temperature=args.temperature,
            )
            state = [
                {
                    "name": s["name"],
                    "perspective": s.get("perspective", ""),
                    "reason": s.get("reason", ""),
                    "event_ids": list(s["event_ids"]),
                    "created_at_event": None,
                }
                for s in initial.get("subtopics") or []
            ]
            entry["steps"].append(
                {"stage": "seed", "event_ids": [e["id"] for e in seed_events],
                 "subtopics_after": len(state)}
            )

            seen = list(seed_events)
            since_compact = 0
            for new_event in rest:
                seen.append(new_event)
                existing_block = "\n".join(
                    f"- {s['name']} ({len(s['event_ids'])}건): {s['reason']}" for s in state
                ) or "(아직 없음)"
                all_block = format_events_block(seen, detail, args.summary_chars)
                new_block = format_events_block([new_event], detail, args.summary_chars)
                before = len(state)

                if args.split_steps:
                    # 2단계: 이 이벤트가 어느 기존 서브토픽에 속하는지 (이벤트 기준으로 묻는다)
                    joined = ask_json(
                        client,
                        build_single_event_membership_prompt(
                            topic["title"], existing_block, new_block
                        ),
                        {"subtopic_names"},
                        temperature=args.temperature,
                    )
                    # 3단계: 그와 **무관하게** 새 관점이 생겼는지
                    fresh = ask_json(
                        client,
                        build_new_perspective_prompt(
                            topic["title"], existing_block, all_block, new_block
                        ),
                        {"new_subtopics"},
                        temperature=args.temperature,
                    )
                    answer = {
                        "assignments": [
                            {"subtopic_name": name, "add_event_ids": [new_event["id"]]}
                            for name in joined.get("subtopic_names") or []
                        ],
                        "new_subtopics": fresh.get("new_subtopics") or [],
                    }
                else:
                    answer = ask_json(
                        client,
                        build_incremental_subtopic_prompt(
                            topic["title"], existing_block, all_block, new_block
                        ),
                        {"assignments"},
                        temperature=args.temperature,
                    )
                notes = apply_increment(state, answer, new_event["id"], valid_ids)
                entry["issues"].extend(f"[{new_event['id']}] {n}" for n in notes)
                homes = [s["name"] for s in state if new_event["id"] in s["event_ids"]]
                step = {
                    "stage": "increment",
                    "event_id": new_event["id"],
                    "assigned_to": homes,
                    "new_subtopics": len(state) - before,
                    "subtopics_after": len(state),
                }

                since_compact += 1
                if args.recompact and since_compact >= args.recompact:
                    since_compact = 0
                    stale = len(state)
                    carry_block = "\n".join(
                        f"- {s['name']}: {s['reason']}" for s in state
                    ) or None
                    rebuilt = run_two_stage(
                        client,
                        topic["title"],
                        all_block,
                        len(seen),
                        [
                            (e["id"], format_events_block([e], detail, args.summary_chars))
                            for e in seen
                        ],
                        temperature=args.temperature,
                        existing_block=carry_block,
                        carry_mode="consolidate",
                    )
                    fresh_state = [
                        {
                            "name": s["name"],
                            "perspective": s.get("perspective", ""),
                            "reason": s.get("reason", ""),
                            "event_ids": list(s["event_ids"]),
                            "created_at_event": None,
                        }
                        for s in rebuilt.get("subtopics") or []
                    ]
                    if fresh_state:
                        step["recompacted"] = {"before": stale, "after": len(fresh_state)}
                        state = fresh_state
                    else:
                        entry["issues"].append(
                            f"[{new_event['id']}] 재생성 결과가 비어 기존 구조를 유지했다"
                        )
                entry["steps"].append(step)
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  [{idx}/{len(targets)}] {topic['title'][:28]} 실패: {exc}", file=sys.stderr)
        else:
            subtopics, standalone, issues = validate({"subtopics": state}, valid_ids)
            entry.update(subtopics=subtopics, standalone_event_ids=standalone)
            entry["issues"].extend(issues)
            print(
                f"  [{idx}/{len(targets)}] {topic['title'][:28]} "
                f"→ 서브토픽 {len(subtopics)} / 단독 {len(standalone)} "
                f"(증분 {len(rest)}회, {time.time()-t0:.0f}s)",
                file=sys.stderr,
            )
        results.append(entry)

    ok = [r for r in results if not r.get("error")]
    payload = {
        "tag": args.tag,
        "snapshot": str(args.snapshot),
        "model": args.model,
        "base_url": args.base_url,
        "mode": "incremental",
        "seed": args.seed,
        "split_steps": args.split_steps,
        "recompact": args.recompact,
        "temperature": args.temperature,
        "min_events": args.min_events,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.time() - started),
        "metrics": measure(ok),
        "topics": results,
    }

    if args.compare and args.compare.exists():
        other = json.loads(args.compare.read_text(encoding="utf-8"))
        wanted = {r["topic_id"] for r in ok}
        rows = [r for r in other["topics"] if not r.get("error") and r["topic_id"] in wanted]
        for r in rows:
            r["event_titles"] = {int(k): v for k, v in r["event_titles"].items()}
        by_topic = {r["topic_id"]: r for r in rows}
        aligns = {}
        for r in ok:
            peer = by_topic.get(r["topic_id"])
            if peer:
                aligns[str(r["topic_id"])] = alignment(r["subtopics"], peer["subtopics"])
        overall = (
            round(sum(a["score"] for a in aligns.values()) / len(aligns), 3) if aligns else 0.0
        )
        payload["baseline"] = {
            "tag": other.get("tag"),
            "metrics": measure(rows),
            "names": {
                str(r["topic_id"]): [s["name"] for s in r["subtopics"]] for r in rows
            },
            "alignment": aligns,
            "alignment_overall": overall,
        }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / f"subtopic-v2-{args.tag}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    m = payload["metrics"]
    lines = [
        f"# 서브토픽 v2 증분 검증: {args.tag}",
        "",
        f"- 스냅샷: `{args.snapshot}` / 모델: `{args.model}`",
        f"- seed {args.seed}개로 최초 생성 후 나머지를 하나씩 투입"
        + (" · 증분 한 걸음을 귀속/새 관점 두 호출로 분리" if args.split_steps else "")
        + (f" · {args.recompact}걸음마다 전체 재생성" if args.recompact else ""),
        f"- 실행: {payload['timestamp']} ({payload['elapsed_seconds']}초)",
        "",
        "## 증분 결과 vs 일괄 생성",
        "",
        "| 지표 | 증분 | 일괄 |",
        "|---|---|---|",
    ]
    base = payload.get("baseline", {}).get("metrics")
    def row(label: str, key: str, fmt: str = "{:.1%}") -> str:
        left = fmt.format(m[key])
        right = fmt.format(base[key]) if base else "—"
        return f"| {label} | {left} | {right} |"

    lines += [
        f"| 서브토픽 수 | {m['subtopics']} | {base['subtopics'] if base else '—'} |",
        row("이벤트당 귀속", "memberships_per_event", "{:.2f}"),
        row("둘 이상에 속한 이벤트", "multi_home_ratio"),
        row("단독 이벤트", "standalone_ratio"),
        row("1건짜리 서브토픽", "single_event_subtopic_ratio"),
        row("이벤트 제목 축약 이름", "event_echo_ratio"),
        "",
    ]
    if payload.get("baseline", {}).get("alignment"):
        lines += [
            f"**일괄 생성과의 구조 일치도: {payload['baseline']['alignment_overall']:.3f}**"
            " (서브토픽을 이벤트 집합으로 보고 양방향 최대 자카드를 평균한 값. 1.0 이면 같은 구조)",
            "",
        ]
    lines.append("## 토픽별 전개")

    for r in results:
        lines += ["", f"### [{r['topic_id']}] {r['topic_title']} — 이벤트 {r['event_count']}개", ""]
        if r.get("error"):
            lines.append(f"**실패**: {r['error']}")
            continue
        for step in r["steps"]:
            if step["stage"] == "seed":
                lines.append(
                    f"- 최초 생성: 이벤트 {len(step['event_ids'])}개 → 서브토픽 {step['subtopics_after']}개"
                )
            else:
                title = r["event_titles"].get(step["event_id"], "")
                homes = ", ".join(step["assigned_to"]) or "(단독)"
                extra = f" · 새 서브토픽 {step['new_subtopics']}개" if step["new_subtopics"] else ""
                if step.get("recompacted"):
                    rc = step["recompacted"]
                    extra += f" · **전체 재생성 {rc['before']}→{rc['after']}개**"
                lines.append(f"- ＋({step['event_id']}) {title[:44]} → {homes}{extra}")
        lines.append("")
        lines.append("최종 구조:")
        for s in r["subtopics"]:
            born = " (증분 생성)" if s.get("created_at_event") else ""
            lines.append(f"  - **{s['name']}**{born} — {len(s['event_ids'])}건")
        if r["standalone_event_ids"]:
            lines.append(f"  - **단독 이벤트** — {len(r['standalone_event_ids'])}건")
        align = payload.get("baseline", {}).get("alignment", {}).get(str(r["topic_id"]))
        if align:
            lines.append(f"  (일괄과의 일치도 {align['score']:.3f})")
        if payload.get("baseline", {}).get("names", {}).get(str(r["topic_id"])):
            names = payload["baseline"]["names"][str(r["topic_id"])]
            lines.append("")
            lines.append(f"  일괄 생성은: {', '.join(names)}")
        if r["issues"]:
            lines.append("")
            lines.append("검증에서 고친 것:")
            lines += [f"  - {i}" for i in r["issues"]]

    md_path = args.out_dir / f"subtopic-v2-{args.tag}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n저장: {json_path}\n      {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
