"""DB 예측값과 gold_labels.json을 비교해 레벨별 클러스터링 지표를 산출한다.

사용 예:
  # 일반 실행 (Postgres 필수)
  python eval/evaluate.py \\
    --database-url "postgresql://..." \\
    --gold eval/data/gold_labels.json \\
    --run-id R1 \\
    --config-tag "evt_dist=0.45,top_k=5"

  # 오라클 자기신뢰 테스트 (DB 불필요 — 3레벨 모두 1.0이 나와야 함)
  python eval/evaluate.py --use-gold-as-pred --gold eval/data/gold_labels.json

  # 랜덤 기저선 확인 (DB 불필요 — 낮은 점수가 나와야 함)
  python eval/evaluate.py --random-pred --seed 42 --gold eval/data/gold_labels.json

gold_labels.json 형식:
  { "<article_guid>": {"gold_topic": str, "gold_subtopic": str, "gold_event": str}, ... }

읽는 DB 테이블·컬럼:
  - event_articles (event_id, article_id)  +  articles (id, guid)
      → 기사 guid → 예측 event_id 매핑 (이벤트 레벨)
  - events (id, topic_id)
      → 이벤트 → 예측 leaf 토픽(= 서브토픽) 매핑
  - topics (id, parent_topic_id)
      → leaf 토픽 → root 토픽 계층 추적 (토픽 레벨)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random as _random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# hannoon-ai/src를 경로에 추가해 collector.storage를 임포트한다.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

# eval/ 디렉터리를 경로에 추가해 metrics 모듈을 임포트한다.
_EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_EVAL_DIR))

from collector.storage import ensure_db  # noqa: E402
from metrics import evaluate_level  # noqa: E402

# ── 상수 ──────────────────────────────────────────────────────────────────

RESULTS_DIR = _EVAL_DIR / "results"
METRICS_CSV = RESULTS_DIR / "metrics.csv"

CSV_COLUMNS = [
    "run_id",
    "config_tag",
    "timestamp",
    # 이벤트 레벨 지표 + 클러스터 진단
    "coverage_event",
    "ari_event",
    "nmi_event",
    "v_measure_event",
    "bcubed_f1_event",
    "num_pred_clusters_event",
    "num_gold_clusters_event",
    "singleton_rate_event",
    "over_split_event",
    "over_merge_event",
    "duplicate_pred_event",
    # 서브토픽 레벨 지표 + 클러스터 진단
    "coverage_subtopic",
    "ari_subtopic",
    "nmi_subtopic",
    "v_measure_subtopic",
    "bcubed_f1_subtopic",
    "num_pred_clusters_subtopic",
    "num_gold_clusters_subtopic",
    "singleton_rate_subtopic",
    "over_split_subtopic",
    "over_merge_subtopic",
    "duplicate_pred_subtopic",
    # 토픽 레벨 지표 + 클러스터 진단
    "coverage_topic",
    "ari_topic",
    "nmi_topic",
    "v_measure_topic",
    "bcubed_f1_topic",
    "num_pred_clusters_topic",
    "num_gold_clusters_topic",
    "singleton_rate_topic",
    "over_split_topic",
    "over_merge_topic",
    "duplicate_pred_topic",
    # covered-only (배정된 아이템만, 유령 싱글턴 제외) — 서브토픽/토픽
    "covered_bcubed_f1_subtopic",
    "covered_bcubed_precision_subtopic",
    "covered_singleton_rate_subtopic",
    "covered_num_pred_subtopic",
    "covered_over_merge_subtopic",
    "covered_bcubed_f1_topic",
    "covered_bcubed_precision_topic",
    "covered_singleton_rate_topic",
    "covered_num_pred_topic",
    "covered_over_merge_topic",
    # 계층 정합성
    "hierarchy_consistency",
]

# ── gold 집계 헬퍼 ────────────────────────────────────────────────────────


def _resolve_event_gold_labels(
    gold_labels: dict[str, dict[str, str]],
    event_pred: dict[str, int],
    field: str,
) -> tuple[dict[int, str], list[str]]:
    """event_id → 대표 gold 라벨 맵과 불일치 경고 목록을 반환한다.

    같은 예측 이벤트의 기사들이 서로 다른 gold[field]를 가지면 경고를 수집한다.
    대표값은 다수결, 동률이면 첫 번째 등장 값(first-wins)으로 결정한다.

    이 헬퍼를 build_subtopic_level_maps / build_topic_level_maps /
    compute_hierarchy_consistency 세 곳이 공유해 집계 로직을 일관화한다.
    """
    event_label_lists: dict[int, list[str]] = {}
    for guid, labels in gold_labels.items():
        if guid not in event_pred:
            continue
        ev_id = event_pred[guid]
        event_label_lists.setdefault(ev_id, []).append(labels[field])

    warnings: list[str] = []
    result: dict[int, str] = {}
    for ev_id, lbl_list in event_label_lists.items():
        unique = set(lbl_list)
        if len(unique) > 1:
            counter = Counter(lbl_list)
            max_count = max(counter.values())
            # 동률이면 목록에서 가장 먼저 등장한 후보(first-wins)
            representative = next(lbl for lbl in lbl_list if counter[lbl] == max_count)
            warnings.append(
                f"이벤트 {ev_id}의 기사들이 서로 다른 {field} 라벨을 가짐: {unique} "
                f"(대표값: {representative!r}, 다수결/first-wins)"
            )
        else:
            representative = lbl_list[0]
        result[ev_id] = representative

    return result, warnings


# ── DB에서 예측 읽기 ──────────────────────────────────────────────────────
#
# 읽는 테이블·컬럼:
#   event_articles.event_id, event_articles.article_id
#   articles.id, articles.guid
#   events.id, events.topic_id
#   topics.id, topics.parent_topic_id


def load_event_predictions(conn) -> dict[str, int]:
    """기사 guid → 예측 event_id 맵을 반환한다.

    event_articles JOIN articles ON articles.id = event_articles.article_id
    """
    rows = conn.query(
        """
        SELECT a.guid, ea.event_id
        FROM event_articles ea
        JOIN articles a ON a.id = ea.article_id
        WHERE a.guid IS NOT NULL
        """
    )
    return {row["guid"]: row["event_id"] for row in rows}


def load_topic_hierarchy(conn) -> dict[int, tuple[int, int]]:
    """topic_id → (leaf_topic_id, root_topic_id) 맵을 반환한다.

    topics.parent_topic_id IS NULL이면 최상위 토픽(root).
    현재 스키마는 최대 2레벨(root → leaf)이지만 더 깊은 계층도 처리한다.
    """
    rows = conn.query("SELECT id, parent_topic_id FROM topics")
    parent: dict[int, int | None] = {row["id"]: row["parent_topic_id"] for row in rows}

    result: dict[int, tuple[int, int]] = {}
    for t_id in parent:
        root = t_id
        visited: set[int] = set()
        while parent.get(root) is not None and root not in visited:
            visited.add(root)
            root = parent[root]  # type: ignore[assignment]
        result[t_id] = (t_id, root)

    return result


def load_event_topic_predictions(conn) -> dict[int, dict[str, int | None]]:
    """event_id → {"leaf_topic_id": int, "root_topic_id": int} 맵을 반환한다.

    events.topic_id가 NULL인 이벤트(미배정)는 결과에서 제외된다.
    """
    rows = conn.query("SELECT id, topic_id FROM events WHERE topic_id IS NOT NULL")
    hierarchy = load_topic_hierarchy(conn)

    result: dict[int, dict[str, int | None]] = {}
    for row in rows:
        event_id: int = row["id"]
        leaf: int = row["topic_id"]
        if leaf in hierarchy:
            _leaf, root = hierarchy[leaf]
        else:
            root = leaf
        result[event_id] = {"leaf_topic_id": leaf, "root_topic_id": root}
    return result


# ── gold 로드 ─────────────────────────────────────────────────────────────


def load_gold_labels(gold_path: str) -> dict[str, dict[str, str]]:
    """gold_labels.json을 로드한다."""
    # utf-8-sig: Windows에서 BOM이 붙은 파일도 투명하게 처리한다
    with open(gold_path, encoding="utf-8-sig") as f:
        return json.load(f)


# ── 레벨별 pred/gold dict 구성 ───────────────────────────────────────────


def build_event_level_maps(
    gold_labels: dict[str, dict[str, str]],
    event_pred: dict[str, int],
) -> tuple[dict[str, int], dict[str, str]]:
    """기사 단위 이벤트 레벨 pred/gold dict를 반환한다."""
    pred: dict[str, int] = {}
    gold: dict[str, str] = {}
    for guid, labels in gold_labels.items():
        gold[guid] = labels["gold_event"]
        if guid in event_pred:
            pred[guid] = event_pred[guid]
    return pred, gold


def build_subtopic_level_maps(
    gold_labels: dict[str, dict[str, str]],
    event_pred: dict[str, int],
    event_topic: dict[int, dict[str, int | None]],
) -> tuple[dict[int, int | None], dict[int, str], list[str]]:
    """이벤트 단위 서브토픽 레벨 pred/gold dict를 반환한다.

    gold 불일치 경고는 _resolve_event_gold_labels를 통해 일관되게 수집된다.
    """
    event_gold_subtopic, warnings = _resolve_event_gold_labels(
        gold_labels, event_pred, "gold_subtopic"
    )
    pred: dict[int, int | None] = {
        ev_id: event_topic[ev_id]["leaf_topic_id"]
        for ev_id in event_gold_subtopic
        if ev_id in event_topic
    }
    return pred, event_gold_subtopic, warnings


def build_topic_level_maps(
    gold_labels: dict[str, dict[str, str]],
    event_pred: dict[str, int],
    event_topic: dict[int, dict[str, int | None]],
) -> tuple[dict[int, int | None], dict[int, str], list[str]]:
    """이벤트 단위 토픽 레벨 pred/gold dict를 반환한다.

    gold 불일치 경고는 _resolve_event_gold_labels를 통해 일관되게 수집된다.
    반환값: (pred_map, event_gold_topic, warnings)
    event_gold_topic은 compute_hierarchy_consistency에 직접 전달한다.
    """
    event_gold_topic, warnings = _resolve_event_gold_labels(
        gold_labels, event_pred, "gold_topic"
    )
    pred: dict[int, int | None] = {
        ev_id: event_topic[ev_id]["root_topic_id"]
        for ev_id in event_gold_topic
        if ev_id in event_topic
    }
    return pred, event_gold_topic, warnings


# ── 계층 정합성 ───────────────────────────────────────────────────────────


def compute_hierarchy_consistency(
    event_gold_topic: dict[int, str],
    event_topic: dict[int, dict[str, int | None]],
) -> float:
    """계층 정합성을 계산한다.

    같은 gold_topic을 가진 이벤트들이 모두 같은 예측 root_topic을 공유하면 정합.
    gold_topic 그룹 내에 2개 이상의 root가 혼재하면 해당 이벤트들은 비정합으로 간주한다.

    Args:
        event_gold_topic: event_id → gold_topic 라벨 (build_topic_level_maps의 gold 반환값)
        event_topic: event_id → {"leaf_topic_id", "root_topic_id"} (DB 또는 합성 예측)
    """
    gold_topic_to_roots: dict[str, set] = {}
    for ev_id, gt in event_gold_topic.items():
        if ev_id not in event_topic:
            continue
        root = event_topic[ev_id]["root_topic_id"]
        gold_topic_to_roots.setdefault(gt, set()).add(root)

    consistent = 0
    total = 0
    for ev_id, gt in event_gold_topic.items():
        if ev_id not in event_topic:
            continue
        total += 1
        if len(gold_topic_to_roots.get(gt, set())) == 1:
            consistent += 1

    return consistent / total if total > 0 else 0.0


# ── 오라클·랜덤 모드 합성 입력 빌더 ─────────────────────────────────────────


def _build_oracle_event_pred(gold_labels: dict) -> dict[str, int]:
    """gold_event 문자열을 정수 ID로 변환해 합성 event_pred를 반환한다.

    --use-gold-as-pred 모드 전용. gold를 그대로 예측으로 쓰는 데이터 랭글링 경로를
    오라클 테스트한다(3레벨 ARI=1.0, bcubed_f1=1.0이 되어야 함).
    """
    unique_events = sorted({v["gold_event"] for v in gold_labels.values()})
    event_str_to_id = {e: i for i, e in enumerate(unique_events)}
    return {guid: event_str_to_id[lbl["gold_event"]] for guid, lbl in gold_labels.items()}


def _build_oracle_event_topic(
    gold_labels: dict,
    event_pred: dict[str, int],
) -> dict[int, dict[str, int]]:
    """gold_subtopic/gold_topic 문자열을 정수 ID로 변환해 합성 event_topic을 반환한다."""
    unique_subtopics = sorted({v["gold_subtopic"] for v in gold_labels.values()})
    unique_topics = sorted({v["gold_topic"] for v in gold_labels.values()})
    subtopic_to_id = {s: i for i, s in enumerate(unique_subtopics)}
    topic_to_id = {t: i for i, t in enumerate(unique_topics)}

    result: dict[int, dict[str, int]] = {}
    for guid, labels in gold_labels.items():
        if guid not in event_pred:
            continue
        ev_id = event_pred[guid]
        if ev_id not in result:
            result[ev_id] = {
                "leaf_topic_id": subtopic_to_id[labels["gold_subtopic"]],
                "root_topic_id": topic_to_id[labels["gold_topic"]],
            }
    return result


def _build_random_event_pred(gold_labels: dict, seed: int) -> dict[str, int]:
    """gold_event 라벨을 무작위 섞어 합성 event_pred를 반환한다.

    stdlib random + 고정 seed로 재현 가능하다.
    """
    rng = _random.Random(seed)
    guids = list(gold_labels.keys())
    events = [gold_labels[g]["gold_event"] for g in guids]
    rng.shuffle(events)
    unique_events = sorted(set(events))
    event_str_to_id = {e: i for i, e in enumerate(unique_events)}
    return {guid: event_str_to_id[ev] for guid, ev in zip(guids, events)}


def _build_random_event_topic(
    gold_labels: dict,
    event_pred: dict[str, int],
    seed: int,
) -> dict[int, dict[str, int]]:
    """이벤트에 무작위 서브토픽/토픽 ID를 배정한다 (stdlib random, 고정 seed)."""
    rng = _random.Random(seed + 1)
    unique_subtopics = sorted({v["gold_subtopic"] for v in gold_labels.values()})
    unique_topics = sorted({v["gold_topic"] for v in gold_labels.values()})
    subtopic_to_id = {s: i for i, s in enumerate(unique_subtopics)}
    topic_to_id = {t: i for i, t in enumerate(unique_topics)}

    result: dict[int, dict[str, int]] = {}
    for ev_id in set(event_pred.values()):
        result[ev_id] = {
            "leaf_topic_id": subtopic_to_id[rng.choice(unique_subtopics)],
            "root_topic_id": topic_to_id[rng.choice(unique_topics)],
        }
    return result


# ── 리포트 생성 ────────────────────────────────────────────────────────────


def _fmt(v) -> str:
    """소수점 4자리 형식으로 지표 값을 출력한다."""
    if v is None:
        return "N/A"
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return str(v)


def write_markdown_report(
    path: Path,
    run_id: str,
    config_tag: str,
    timestamp: str,
    event_metrics: dict,
    subtopic_metrics: dict,
    topic_metrics: dict,
    hierarchy_consistency: float,
    warnings: list[str],
) -> None:
    """사람이 읽기 쉬운 마크다운 리포트를 작성한다."""

    def level_block(title: str, m: dict) -> list[str]:
        diag = m.get("diagnostics", {})
        lines = [
            f"## {title}",
            "",
            "| 지표 | 값 |",
            "|---|---|",
            f"| coverage | {_fmt(m.get('coverage'))} |",
            f"| ARI | {_fmt(m.get('ari'))} |",
            f"| NMI | {_fmt(m.get('nmi'))} |",
            f"| V-measure | {_fmt(m.get('v_measure'))} |",
            f"| Homogeneity | {_fmt(m.get('homogeneity'))} |",
            f"| Completeness | {_fmt(m.get('completeness'))} |",
            f"| B-cubed Precision | {_fmt(m.get('bcubed_precision'))} |",
            f"| B-cubed Recall | {_fmt(m.get('bcubed_recall'))} |",
            f"| B-cubed F1 | {_fmt(m.get('bcubed_f1'))} |",
            "",
            "### 진단",
            "",
        ]
        for k, v in diag.items():
            lines.append(f"- **{k}**: {v}")
        co = m.get("covered_only")
        if co:
            co_diag = co.get("diagnostics", {})
            lines += [
                "",
                "### covered-only (배정된 아이템만, 유령 싱글턴 제외)",
                "",
                f"- **B-cubed Precision**: {_fmt(co.get('bcubed_precision'))}",
                f"- **B-cubed Recall**: {_fmt(co.get('bcubed_recall'))}",
                f"- **B-cubed F1**: {_fmt(co.get('bcubed_f1'))}",
                f"- **ARI**: {_fmt(co.get('ari'))}",
                f"- **num_pred_clusters**: {co_diag.get('num_pred_clusters')}",
                f"- **singleton_rate**: {co_diag.get('singleton_rate')}",
                f"- **over_split_count**: {co_diag.get('over_split_count')}",
                f"- **over_merge_count**: {co_diag.get('over_merge_count')}",
            ]
        lines += ["", "---", ""]
        return lines

    body: list[str] = [
        f"# 평가 리포트: {run_id}",
        "",
        f"- **config_tag**: {config_tag}",
        f"- **timestamp**: {timestamp}",
        "",
        "---",
        "",
    ]
    body += level_block("이벤트 레벨", event_metrics)
    body += level_block("서브토픽 레벨", subtopic_metrics)
    body += level_block("토픽 레벨", topic_metrics)
    body += [
        "## 계층 정합성",
        "",
        f"- **hierarchy_consistency**: {_fmt(hierarchy_consistency)}",
        "",
    ]
    if warnings:
        body += ["---", "", "## 경고", ""]
        for w in warnings:
            body.append(f"- {w}")

    path.write_text("\n".join(body), encoding="utf-8")


def _ensure_csv_header(path: Path, columns: list[str]) -> None:
    """CSV 헤더가 columns와 다를 경우 기존 행을 보존하며 헤더를 마이그레이션한다.

    파일이 없으면 아무것도 하지 않는다(append_csv_row가 헤더를 새로 쓴다).
    헤더가 일치하면 그대로 둔다.
    헤더가 다르면 기존 행을 모두 읽고 새 헤더로 전체를 다시 쓴다.
    기존 행에 없는 새 컬럼은 빈 문자열로 채운다.
    """
    if not path.exists():
        return

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing_columns = list(reader.fieldnames or [])
        if existing_columns == columns:
            return  # 헤더 동일 — 마이그레이션 불필요
        rows = list(reader)

    # 헤더 불일치 → 기존 데이터를 새 헤더 기준으로 재기록한다
    print(
        f"[CSV 마이그레이션] 헤더 변경 감지 — {len(rows)}개 기존 행을 새 헤더로 재기록합니다.",
        file=sys.stderr,
    )
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in columns})


def append_csv_row(
    run_id: str,
    config_tag: str,
    timestamp: str,
    event_m: dict,
    subtopic_m: dict,
    topic_m: dict,
    hierarchy_consistency: float,
) -> None:
    """metrics.csv에 한 행을 추가한다.

    파일이 없으면 헤더를 먼저 쓴다.
    기존 파일의 헤더가 CSV_COLUMNS와 다르면 _ensure_csv_header가 마이그레이션 후 추가한다.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_csv_header(METRICS_CSV, CSV_COLUMNS)
    write_header = not METRICS_CSV.exists()

    event_diag = event_m.get("diagnostics", {})
    subtopic_diag = subtopic_m.get("diagnostics", {})
    topic_diag = topic_m.get("diagnostics", {})
    sub_co = subtopic_m.get("covered_only", {})
    sub_co_diag = sub_co.get("diagnostics", {})
    topic_co = topic_m.get("covered_only", {})
    topic_co_diag = topic_co.get("diagnostics", {})

    with open(METRICS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "run_id": run_id,
                "config_tag": config_tag,
                "timestamp": timestamp,
                # 이벤트 레벨
                "coverage_event": event_m.get("coverage", ""),
                "ari_event": event_m.get("ari", ""),
                "nmi_event": event_m.get("nmi", ""),
                "v_measure_event": event_m.get("v_measure", ""),
                "bcubed_f1_event": event_m.get("bcubed_f1", ""),
                "num_pred_clusters_event": event_diag.get("num_pred_clusters", ""),
                "num_gold_clusters_event": event_diag.get("num_gold_clusters", ""),
                "singleton_rate_event": event_diag.get("singleton_rate", ""),
                "over_split_event": event_diag.get("over_split_count", ""),
                "over_merge_event": event_diag.get("over_merge_count", ""),
                "duplicate_pred_event": event_diag.get("duplicate_pred_clusters", ""),
                # 서브토픽 레벨
                "coverage_subtopic": subtopic_m.get("coverage", ""),
                "ari_subtopic": subtopic_m.get("ari", ""),
                "nmi_subtopic": subtopic_m.get("nmi", ""),
                "v_measure_subtopic": subtopic_m.get("v_measure", ""),
                "bcubed_f1_subtopic": subtopic_m.get("bcubed_f1", ""),
                "num_pred_clusters_subtopic": subtopic_diag.get("num_pred_clusters", ""),
                "num_gold_clusters_subtopic": subtopic_diag.get("num_gold_clusters", ""),
                "singleton_rate_subtopic": subtopic_diag.get("singleton_rate", ""),
                "over_split_subtopic": subtopic_diag.get("over_split_count", ""),
                "over_merge_subtopic": subtopic_diag.get("over_merge_count", ""),
                "duplicate_pred_subtopic": subtopic_diag.get("duplicate_pred_clusters", ""),
                # 토픽 레벨
                "coverage_topic": topic_m.get("coverage", ""),
                "ari_topic": topic_m.get("ari", ""),
                "nmi_topic": topic_m.get("nmi", ""),
                "v_measure_topic": topic_m.get("v_measure", ""),
                "bcubed_f1_topic": topic_m.get("bcubed_f1", ""),
                "num_pred_clusters_topic": topic_diag.get("num_pred_clusters", ""),
                "num_gold_clusters_topic": topic_diag.get("num_gold_clusters", ""),
                "singleton_rate_topic": topic_diag.get("singleton_rate", ""),
                "over_split_topic": topic_diag.get("over_split_count", ""),
                "over_merge_topic": topic_diag.get("over_merge_count", ""),
                "duplicate_pred_topic": topic_diag.get("duplicate_pred_clusters", ""),
                # covered-only (유령 싱글턴 제외)
                "covered_bcubed_f1_subtopic": sub_co.get("bcubed_f1", ""),
                "covered_bcubed_precision_subtopic": sub_co.get("bcubed_precision", ""),
                "covered_singleton_rate_subtopic": sub_co_diag.get("singleton_rate", ""),
                "covered_num_pred_subtopic": sub_co_diag.get("num_pred_clusters", ""),
                "covered_over_merge_subtopic": sub_co_diag.get("over_merge_count", ""),
                "covered_bcubed_f1_topic": topic_co.get("bcubed_f1", ""),
                "covered_bcubed_precision_topic": topic_co.get("bcubed_precision", ""),
                "covered_singleton_rate_topic": topic_co_diag.get("singleton_rate", ""),
                "covered_num_pred_topic": topic_co_diag.get("num_pred_clusters", ""),
                "covered_over_merge_topic": topic_co_diag.get("over_merge_count", ""),
                # 계층 정합성
                "hierarchy_consistency": hierarchy_consistency,
            }
        )


# ── 공통 평가 파이프라인 ───────────────────────────────────────────────────


def _run_pipeline(
    gold_labels: dict,
    event_pred: dict,
    event_topic: dict,
    run_id: str,
    config_tag: str,
    timestamp: str,
    oracle_mode: bool = False,
) -> None:
    """gold/pred 매핑을 받아 3레벨 지표를 계산하고 리포트·CSV를 저장한다."""
    pred_event, gold_event = build_event_level_maps(gold_labels, event_pred)
    pred_subtopic, gold_subtopic, sub_warns = build_subtopic_level_maps(
        gold_labels, event_pred, event_topic
    )
    pred_topic, gold_topic, topic_warns = build_topic_level_maps(
        gold_labels, event_pred, event_topic
    )

    all_warnings: list[str] = sub_warns + topic_warns
    for w in all_warnings:
        print(f"[경고] {w}", file=sys.stderr)

    event_metrics = evaluate_level(pred_event, gold_event)
    subtopic_metrics = evaluate_level(pred_subtopic, gold_subtopic)
    topic_metrics = evaluate_level(pred_topic, gold_topic)
    # gold_topic은 build_topic_level_maps가 반환한 event_id→gold_topic 맵을 재사용한다
    hier_consistency = compute_hierarchy_consistency(gold_topic, event_topic)

    # coverage 가드: 배치 미완료로 지표가 무의미해지는 상황을 절대 못 놓치게 한다
    coverage = event_metrics.get("coverage", 1.0)
    if coverage < 1.0 and not oracle_mode:
        warn_cov = (
            f"[경고] ★★★ 이벤트 레벨 coverage={coverage:.4f} < 1.0 ★★★ — "
            "일부 기사가 DB에서 이벤트에 배정되지 않았습니다. "
            "배치가 완전히 완료됐는지 확인하세요. 지표 해석에 주의가 필요합니다."
        )
        print(warn_cov, file=sys.stderr)
        all_warnings.append(warn_cov)

    # 오라클 모드 확인 출력
    if oracle_mode:
        print("[오라클 모드] 3레벨 ARI/bcubed_f1 확인:")
        for lv_name, m in [
            ("이벤트  ", event_metrics),
            ("서브토픽", subtopic_metrics),
            ("토픽    ", topic_metrics),
        ]:
            ari = m.get("ari", 0.0)
            bf1 = m.get("bcubed_f1", 0.0)
            ok = abs(ari - 1.0) < 1e-4 and abs(bf1 - 1.0) < 1e-4
            status = "OK" if ok else "FAIL"
            print(f"  [{status}] {lv_name}: ARI={ari:.4f}, bcubed_f1={bf1:.4f}")

    # 리포트·CSV 저장
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = RESULTS_DIR / f"{run_id}.md"
    write_markdown_report(
        path=report_path,
        run_id=run_id,
        config_tag=config_tag,
        timestamp=timestamp,
        event_metrics=event_metrics,
        subtopic_metrics=subtopic_metrics,
        topic_metrics=topic_metrics,
        hierarchy_consistency=hier_consistency,
        warnings=all_warnings,
    )
    append_csv_row(
        run_id=run_id,
        config_tag=config_tag,
        timestamp=timestamp,
        event_m=event_metrics,
        subtopic_m=subtopic_metrics,
        topic_m=topic_metrics,
        hierarchy_consistency=hier_consistency,
    )

    print()
    print(f"[완료] 리포트: {report_path}")
    print(f"        CSV  : {METRICS_CSV}")
    print()
    print("── 이벤트 레벨 ─────────────────────────────")
    print(f"  coverage   : {event_metrics.get('coverage', 0):.4f}")
    print(f"  ARI        : {event_metrics.get('ari', 0):.4f}")
    print(f"  B-cubed F1 : {event_metrics.get('bcubed_f1', 0):.4f}")
    print(f"  V-measure  : {event_metrics.get('v_measure', 0):.4f}")
    print(
        f"  singleton% : "
        f"{event_metrics.get('diagnostics', {}).get('singleton_rate', 0):.4f}"
    )
    print()
    print("── 서브토픽 레벨 ───────────────────────────")
    print(f"  coverage   : {subtopic_metrics.get('coverage', 0):.4f}")
    print(f"  ARI        : {subtopic_metrics.get('ari', 0):.4f}")
    print(f"  B-cubed F1 : {subtopic_metrics.get('bcubed_f1', 0):.4f}")
    print()
    print("── 토픽 레벨 ───────────────────────────────")
    print(f"  coverage   : {topic_metrics.get('coverage', 0):.4f}")
    print(f"  ARI        : {topic_metrics.get('ari', 0):.4f}")
    print(f"  B-cubed F1 : {topic_metrics.get('bcubed_f1', 0):.4f}")
    print()
    print(f"── 계층 정합성 : {hier_consistency:.4f}")


# ── CLI 진입점 ────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DB 예측값과 gold_labels.json을 비교해 클러스터링 지표를 산출한다."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres 연결 URL (DATABASE_URL 환경변수로도 지정 가능)",
    )
    parser.add_argument(
        "--gold",
        default=str(_EVAL_DIR / "data" / "gold_labels.json"),
        help="gold_labels.json 파일 경로",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="실행 식별자 (생략 시 UTC 타임스탬프 자동 생성)",
    )
    parser.add_argument(
        "--config-tag",
        default="",
        help="설정 설명 태그 (예: 'evt_dist=0.45,top_k=5')",
    )
    parser.add_argument(
        "--use-gold-as-pred",
        action="store_true",
        help=(
            "gold_labels.json을 그대로 예측으로 사용하는 오라클 자기신뢰 테스트. "
            "DB 불필요. 3레벨 모두 ARI=1.0, bcubed_f1=1.0이 나와야 함."
        ),
    )
    parser.add_argument(
        "--random-pred",
        action="store_true",
        help=(
            "gold 라벨을 무작위 배정한 예측으로 평가하는 기저선 확인. "
            "DB 불필요. 낮은 점수가 나와야 함."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="--random-pred 에 사용할 랜덤 시드 (기본: 42). 재현 가능성을 위해 고정.",
    )
    args = parser.parse_args()

    # run_id: 인자로 받은 경우 그대로 사용, 없으면 UTC 타임스탬프 자동 생성
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"[평가 시작] run_id={run_id}, config_tag={args.config_tag!r}")

    gold_labels = load_gold_labels(args.gold)
    print(f"[gold] {args.gold} - 기사 {len(gold_labels)}건 로드 완료")

    if args.use_gold_as_pred:
        print("[오라클 모드] gold_labels를 그대로 예측으로 사용. DB 연결 불필요.")
        event_pred = _build_oracle_event_pred(gold_labels)
        event_topic = _build_oracle_event_topic(gold_labels, event_pred)
        _run_pipeline(
            gold_labels, event_pred, event_topic,
            run_id, args.config_tag, timestamp, oracle_mode=True,
        )

    elif args.random_pred:
        print(f"[랜덤 모드] seed={args.seed}로 무작위 예측 생성. DB 연결 불필요.")
        event_pred = _build_random_event_pred(gold_labels, args.seed)
        event_topic = _build_random_event_topic(gold_labels, event_pred, args.seed)
        _run_pipeline(
            gold_labels, event_pred, event_topic,
            run_id, args.config_tag, timestamp, oracle_mode=False,
        )

    else:
        if not args.database_url:
            print(
                "[오류] --database-url 또는 DATABASE_URL 환경변수가 필요합니다.",
                file=sys.stderr,
            )
            sys.exit(1)

        print("[DB] 연결 중...")
        conn = ensure_db("", database_url=args.database_url)

        print("[예측] event_articles + articles에서 기사→이벤트 매핑 로드 중...")
        event_pred = load_event_predictions(conn)
        print(f"[예측] {len(event_pred)}건의 기사→이벤트 매핑 로드 완료")

        print("[예측] events + topics에서 이벤트→토픽 계층 로드 중...")
        event_topic = load_event_topic_predictions(conn)
        print(f"[예측] {len(event_topic)}건의 이벤트→토픽 계층 로드 완료")

        conn.close()

        _run_pipeline(
            gold_labels, event_pred, event_topic,
            run_id, args.config_tag, timestamp, oracle_mode=False,
        )


if __name__ == "__main__":
    main()
