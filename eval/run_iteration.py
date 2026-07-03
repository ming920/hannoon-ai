#!/usr/bin/env python3
"""평가 하네스 한 사이클을 오케스트레이션한다.

실행 순서:
    1. reset_test_db.py          — 분류기 출력 초기화
    2. ingest_dummy.py           — 더미 기사 주입
    3. [이벤트 드레인 루프]      — classify_events.py 반복 실행
                                   (article_ai_results.status='done' 잔량 0 될 때까지)
    4. [인-프로세스] article_count 재동기화
       — bare docker pgvector 등 트리거 없는 DB 에서 article_count=0 함정 방지
    5. [토픽 드레인 루프]        — classify_topics.py 반복 실행
                                   (topic_id IS NULL 이벤트 잔량 0 될 때까지)
    6. evaluate.py               — 지표 산출 및 결과 저장

드레인 방식:
    분류기는 배치 크기(EVENT_BATCH_SIZE 기본 5 / TOPIC_BATCH_SIZE 기본 10)만큼
    처리하고 종료한다. .env 의 load_dotenv(override=True) 가 자식 프로세스에서도
    환경변수를 덮어쓰기 때문에, 부모에서 배치 크기를 주입해도 효과가 없다.
    대신 각 분류기를 DB 잔량이 0 이 될 때까지 반복 호출하는 드레인 루프를 사용한다.
    ─ 이벤트 드레인: article_ai_results.status='done' 행 수가 0 이 될 때까지
    ─ 토픽  드레인: events.topic_id IS NULL AND article_count >= N 행 수가 0 이 될 때까지

파라미터 튜닝:
    분류기들은 src/event_classifier/settings.py, src/embedding.py,
    src/openai_client/client.py 에서 load_dotenv(override=True) 를 호출하므로,
    .env 에 존재하는 값은 프로세스 환경변수보다 항상 우선한다.
    따라서 `EVENT_DISTANCE_THRESHOLD=0.40 python eval/run_iteration.py ...` 처럼
    인라인 export 를 해도 .env 가 덮어쓰기 때문에 효과가 없다.

    올바른 튜닝 방법은 --set 플래그를 사용하는 것이다:
        python eval/run_iteration.py \\
            --run-id run-002 \\
            --set EVENT_DISTANCE_THRESHOLD=0.40 \\
            --set TOPIC_DISTANCE_THRESHOLD=0.48

    --set 은 이번 실행에 한해 .env 를 임시 패치하고, 실행 후 원본을 복원한다.

사용 예:
    python eval/run_iteration.py \\
        --database-url "postgresql://localhost/hannoon_eval" \\
        --run-id run-001 \\
        --config-tag "baseline-threshold-045"

    python eval/run_iteration.py \\
        --database-url "postgresql://localhost/hannoon_eval" \\
        --run-id run-003 \\
        --config-tag "lower-threshold" \\
        --set EVENT_DISTANCE_THRESHOLD=0.40 \\
        --set TOPIC_DISTANCE_THRESHOLD=0.48
"""

import argparse
import json
import os
import re
import subprocess
import sys

# src/ 경로를 Python 모듈 검색 경로에 추가한다 (article_count 재동기화 인-프로세스용).
_EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_EVAL_DIR)
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))

from collector.storage import ensure_db  # noqa: E402

_DEFAULT_ARTICLES = os.path.join(_EVAL_DIR, "data", "dummy_articles.json")
_DEFAULT_GOLD = os.path.join(_EVAL_DIR, "data", "gold_labels.json")

# --set KEY=VALUE 의 KEY 유효성 검사: 대문자로 시작하고 대문자·숫자·밑줄만 허용한다.
_ENV_KEY_RE = re.compile(r'^[A-Z][A-Z0-9_]*$')


def _patch_dotenv(dotenv_path: str, overrides: list) -> str:
    """.env 파일에 KEY=VALUE 목록을 적용하고, 패치 전 원본 텍스트를 반환한다.

    동작:
    - KEY= 로 시작하는 기존 줄이 있으면 그 줄 전체를 KEY=VALUE 로 교체한다.
    - 해당 키가 없으면 파일 끝에 새 줄을 추가한다.
    - 주석(#)·빈 줄·기타 줄은 그대로 보존한다.
    반환값: 복원에 사용할 원본 텍스트 (UTF-8).
    """
    with open(dotenv_path, encoding="utf-8") as f:
        original = f.read()

    lines = original.splitlines(keepends=True)

    for kv in overrides:
        key, value = kv.split("=", 1)
        prefix = f"{key}="
        replaced = False
        for i, line in enumerate(lines):
            # 공백 없이 KEY= 로 시작하는 줄만 교체한다 (주석 줄 제외).
            if line.startswith(prefix):
                lines[i] = f"{key}={value}\n"
                replaced = True
                break
        if not replaced:
            # 파일 끝 줄바꿈이 없으면 보장한 뒤 새 줄을 추가한다.
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(f"{key}={value}\n")

    patched = "".join(lines)
    with open(dotenv_path, "w", encoding="utf-8") as f:
        f.write(patched)

    return original


def _run_step(step_label: str, cmd: list, env: dict) -> None:
    """단계를 subprocess 로 실행하고, 실패하면 stderr 를 출력한 뒤 종료한다."""
    print(f"\n[단계 시작] {step_label}")
    result = subprocess.run(cmd, env=env, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(
            f"\n[실패] '{step_label}' 단계가 종료 코드 {result.returncode} 로 실패했습니다.",
            file=sys.stderr,
        )
        if result.stderr:
            print("--- 오류 출력 ---", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            print("-----------------", file=sys.stderr)
        sys.exit(result.returncode)
    print(f"[완료] {step_label}")


def _resync_article_counts(db_url: str) -> None:
    """이벤트별 기사 수를 event_articles 실제 행 수로 재동기화한다.

    hannoon-supabase 스키마에는 event_articles INSERT 시 article_count 를 올리는
    트리거(update_event_counts_on_article_insert)가 있지만,
    bare docker pgvector 같이 트리거 없는 DB 는 article_count 가 0 에 머문다.
    토픽 분류기는 article_count >= TOPIC_MIN_NET_ARTICLE_COUNT 조건으로
    처리 대상 이벤트를 선정하므로, article_count=0 이면 토픽 레벨 지표가 전부 0 이 된다.

    트리거가 있는 DB 에서도 이 UPDATE 는 같은 값으로 덮어쓰므로 무해하다(멱등).
    """
    print("\n[단계 4.5] 이벤트 기사 수 재동기화 (article_count)")
    conn = ensure_db("", database_url=db_url)
    try:
        conn.execute(
            """
            UPDATE events
            SET article_count = (
                SELECT COUNT(*)
                FROM event_articles ea
                WHERE ea.event_id = events.id
            )
            """
        )
        row = conn.query_one(
            """
            SELECT COUNT(*) AS event_count,
                   COALESCE(SUM(article_count), 0) AS total_articles
            FROM events
            """
        )
        if row:
            print(
                f"  재동기화 완료: 이벤트 {row['event_count']}개, "
                f"기사 합계 {row['total_articles']}건"
            )
    finally:
        conn.close()


def _count_done_articles(db_url: str) -> int:
    """이벤트 미배정 기사(status='done') 수를 반환한다."""
    conn = ensure_db("", database_url=db_url)
    try:
        row = conn.query_one(
            "SELECT COUNT(*) AS cnt FROM article_ai_results WHERE status = 'done'"
        )
        return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def _count_unassigned_events(db_url: str, min_net: int) -> int:
    """토픽 미배정 이벤트(topic_id IS NULL, article_count >= min_net) 수를 반환한다."""
    conn = ensure_db("", database_url=db_url)
    try:
        row = conn.query_one(
            "SELECT COUNT(*) AS cnt FROM events WHERE topic_id IS NULL AND article_count >= ?",
            (min_net,),
        )
        return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def _topic_only_reset(db_url: str) -> None:
    """이벤트 레이어를 보존한 채 토픽 레이어만 초기화한다.

    가드: events 테이블이 비어 있으면 오류를 출력하고 비정상 종료한다.
    (이벤트가 없으면 재실행할 대상 자체가 없다.)

    FK-안전 순서로 한 트랜잭션 안에서 실행한다:
      1. UPDATE events SET topic_id = NULL   — 이벤트와 토픽 연결 해제
      2. DELETE FROM topic_causes            — 토픽 원인 벡터 전체 삭제
      3. DELETE FROM topics                  — 토픽 전체 삭제
    event_articles·article_ai_results·임베딩은 그대로 보존된다.
    """
    conn = ensure_db("", database_url=db_url)
    try:
        # 가드: 이벤트가 1건 이상 존재해야 --topics-only 재실행이 의미 있다.
        row = conn.query_one("SELECT COUNT(*) AS cnt FROM events")
        event_count = int(row["cnt"]) if row else 0
        if event_count == 0:
            print(
                "오류: events 테이블에 이벤트가 없습니다. "
                "--topics-only 는 이전 전체 실행(full run)으로 이벤트가 적재된 "
                "DB 에서만 사용할 수 있습니다.",
                file=sys.stderr,
            )
            sys.exit(1)

        print(
            f"\n[토픽 전용 초기화] 이벤트 {event_count}개를 보존하고 토픽 레이어만 초기화합니다."
        )
        with conn.transaction():
            conn.execute("UPDATE events SET topic_id = NULL")
            conn.execute("DELETE FROM topic_causes")
            conn.execute("DELETE FROM topics")

        print(
            f"  완료: 이벤트 {event_count}개의 topic_id 를 NULL 로 초기화하고 "
            "topic_causes·topics 를 전부 삭제했습니다."
        )
    finally:
        conn.close()


def _drain_events(db_url: str, cmd_base: list, env: dict, article_count: int) -> None:
    """이벤트 분류기를 status='done' 기사가 없어질 때까지 반복 실행한다.

    한 패스에서 잔량이 줄지 않으면(stuck) 경고를 출력하고 루프를 중단한다.
    최대 반복 횟수(hard cap) = max(50, article_count).
    """
    hard_cap = max(50, article_count)
    remaining = _count_done_articles(db_url)
    print(f"\n[이벤트 드레인 시작] 처리 대기 기사: {remaining}건, 최대 반복: {hard_cap}회")

    for pass_num in range(1, hard_cap + 1):
        if remaining == 0:
            print(f"[이벤트 드레인] 처리 완료 (총 {pass_num - 1}패스)")
            return

        # 분류기 1회 실행
        result = subprocess.run(cmd_base, env=env, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            print(
                f"\n[실패] 이벤트 드레인 패스 {pass_num} 종료 코드 {result.returncode}.",
                file=sys.stderr,
            )
            if result.stderr:
                print("--- 오류 출력 ---", file=sys.stderr)
                print(result.stderr, file=sys.stderr)
                print("-----------------", file=sys.stderr)
            sys.exit(result.returncode)

        prev_remaining = remaining
        remaining = _count_done_articles(db_url)
        print(
            f"[이벤트 드레인] 패스 {pass_num}: 처리 대기 {prev_remaining} → {remaining}건"
        )

        if remaining >= prev_remaining:
            # 잔량이 줄지 않음 — 영구 실패 기사가 있을 가능성이 높음
            print(
                f"경고: 이벤트 드레인 패스 {pass_num} 에서 잔량이 줄지 않았습니다 "
                f"({prev_remaining} → {remaining}). 루프를 중단합니다.",
                file=sys.stderr,
            )
            return

    # hard_cap 소진
    print(
        f"경고: 이벤트 드레인이 최대 반복 횟수({hard_cap}회)를 초과했습니다. "
        f"남은 기사: {remaining}건.",
        file=sys.stderr,
    )


def _drain_topics(db_url: str, cmd_base: list, env: dict, article_count: int) -> None:
    """토픽 분류기를 미배정 이벤트가 없어질 때까지 반복 실행한다.

    TOPIC_MIN_NET_ARTICLE_COUNT 환경변수(기본값 5)를 읽어 자격 기준으로 사용한다.
    events 테이블에 abusing_count 컬럼이 없으므로 article_count 만으로 필터링한다.
    (src/db/events.py FETCH_UNASSIGNED_SQL 참고: article_count >= ? 만 사용)
    한 패스에서 잔량이 줄지 않으면(stuck) 경고를 출력하고 루프를 중단한다.
    최대 반복 횟수(hard cap) = max(50, article_count).
    """
    min_net = int(os.environ.get("TOPIC_MIN_NET_ARTICLE_COUNT", "5"))
    hard_cap = max(50, article_count)
    remaining = _count_unassigned_events(db_url, min_net)
    print(
        f"\n[토픽 드레인 시작] 미배정 이벤트: {remaining}건 "
        f"(article_count >= {min_net}), 최대 반복: {hard_cap}회"
    )

    for pass_num in range(1, hard_cap + 1):
        if remaining == 0:
            print(f"[토픽 드레인] 처리 완료 (총 {pass_num - 1}패스)")
            return

        # 분류기 1회 실행
        result = subprocess.run(cmd_base, env=env, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            print(
                f"\n[실패] 토픽 드레인 패스 {pass_num} 종료 코드 {result.returncode}.",
                file=sys.stderr,
            )
            if result.stderr:
                print("--- 오류 출력 ---", file=sys.stderr)
                print(result.stderr, file=sys.stderr)
                print("-----------------", file=sys.stderr)
            sys.exit(result.returncode)

        prev_remaining = remaining
        remaining = _count_unassigned_events(db_url, min_net)
        print(
            f"[토픽 드레인] 패스 {pass_num}: 미배정 이벤트 {prev_remaining} → {remaining}건"
        )

        if remaining >= prev_remaining:
            # 잔량이 줄지 않음 — 임계치 미달 이벤트만 남은 경우 등
            print(
                f"경고: 토픽 드레인 패스 {pass_num} 에서 잔량이 줄지 않았습니다 "
                f"({prev_remaining} → {remaining}). 루프를 중단합니다.",
                file=sys.stderr,
            )
            return

    # hard_cap 소진
    print(
        f"경고: 토픽 드레인이 최대 반복 횟수({hard_cap}회)를 초과했습니다. "
        f"남은 이벤트: {remaining}건.",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="평가 하네스 한 사이클(리셋→주입→분류→평가)을 실행합니다."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres 연결 URL (미지정 시 DATABASE_URL 환경변수 사용)",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="이번 실행의 고유 식별자 (예: run-001, exp-threshold-040). "
             "results/<run-id>.md 파일명과 metrics.csv 행에 사용된다. "
             "재현·비교를 위해 호출자가 직접 지정해야 한다.",
    )
    parser.add_argument(
        "--config-tag",
        default="default",
        help="파라미터/프롬프트 구성 설명 태그 (metrics.csv 기록용, "
             "예: 'threshold-040-no-cat-filter')",
    )
    parser.add_argument(
        "--articles",
        default=_DEFAULT_ARTICLES,
        help="더미 기사 JSON 파일 경로 (기본값: eval/data/dummy_articles.json)",
    )
    parser.add_argument(
        "--gold",
        default=_DEFAULT_GOLD,
        help="정답 라벨 JSON 파일 경로 (기본값: eval/data/gold_labels.json)",
    )
    parser.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        dest="set_overrides",
        help="이번 실행에 한해 .env 의 튜닝 파라미터를 임시로 덮어쓴다 (반복 지정 가능). "
             "분류기들이 load_dotenv(override=True) 를 사용하므로 프로세스 환경변수 주입은 "
             "효과가 없다. 이 플래그가 .env 를 직접 패치하는 유일한 안전한 방법이다. "
             "실행 후 원본 .env 는 자동으로 복원된다. "
             "예: --set EVENT_DISTANCE_THRESHOLD=0.40 --set TOPIC_CANDIDATE_LIMIT=10",
    )
    parser.add_argument(
        "--topics-only",
        action="store_true",
        help="이벤트 레이어를 고정하고 토픽 분류만 재실행한다. "
             "DB 초기화·기사 주입·이벤트 드레인 단계를 건너뛰고, "
             "topic_id·topic_causes·topics 만 초기화한 뒤 토픽 드레인→평가를 수행한다. "
             "토픽 프롬프트·임계치 A/B 테스트 시 이벤트 클러스터링 분산을 제거하는 데 사용한다. "
             "반드시 이전 전체 실행(full run)으로 이벤트가 적재된 DB 에서 사용해야 한다.",
    )
    args = parser.parse_args()

    if not args.database_url:
        print(
            "오류: --database-url 또는 DATABASE_URL 환경변수를 지정하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    db_url = args.database_url
    py = sys.executable  # 현재 인터프리터를 자식에도 사용

    # 기사 수 파악: 드레인 루프 hard_cap 계산과 로깅에만 사용한다.
    # (배치 크기 주입은 .env load_dotenv(override=True) 에 의해 무효화되므로 사용하지 않는다.)
    n_articles: int = 0
    try:
        with open(args.articles, encoding="utf-8") as f:
            n_articles = len(json.load(f))
        print(f"더미 기사 수 감지: {n_articles}건")
    except (OSError, json.JSONDecodeError) as e:
        print(
            f"경고: 기사 파일 읽기 실패 ({e}), 드레인 루프 hard_cap 을 기본값(50)으로 설정합니다.",
            file=sys.stderr,
        )

    # 기본 환경: 현재 환경을 상속하고 DATABASE_URL 을 명시적으로 주입한다.
    # EVENT_DISTANCE_THRESHOLD 등 튜닝 환경변수는 그대로 상속된다.
    base_env = os.environ.copy()
    base_env["DATABASE_URL"] = db_url

    # reset 단계: 안전 가드 통과에 필요한 환경변수를 추가한다.
    reset_env = base_env.copy()
    reset_env["EVAL_ALLOW_DESTRUCTIVE_RESET"] = "1"

    # classify_events 환경: 튜닝 변수는 base_env 로 상속된다.
    # EVENT_BATCH_SIZE 주입은 .env load_dotenv(override=True) 에 의해 무효화되므로 제거됨.
    events_env = base_env.copy()

    # classify_topics 환경: 서브토픽 활성화.
    # TOPIC_BATCH_SIZE 주입도 동일 이유로 제거됨.
    topics_env = base_env.copy()
    topics_env["TOPIC_SUBTOPICS_ENABLED"] = "true"

    # ── --set 처리: .env 임시 패치 ────────────────────────────────────────────
    # 분류기들이 load_dotenv(override=True) 를 호출하므로, 튜닝 파라미터는
    # 프로세스 환경변수가 아닌 .env 파일을 직접 수정해야만 적용된다.
    set_overrides = args.set_overrides or []
    dotenv_path = os.path.join(_REPO_ROOT, ".env")
    _original_dotenv = None  # None 이면 복원 불필요 (--set 미사용)

    if set_overrides:
        # 형식 및 키 이름 검증 — .env 에 손대기 전에 실패해야 한다.
        for kv in set_overrides:
            if "=" not in kv:
                print(
                    f"오류: --set 인수 '{kv}' 는 KEY=VALUE 형식이어야 합니다.",
                    file=sys.stderr,
                )
                sys.exit(1)
            key = kv.split("=", 1)[0]
            if not _ENV_KEY_RE.match(key):
                print(
                    f"오류: --set 키 '{key}' 는 대문자로 시작하고 대문자·숫자·밑줄만 "
                    f"포함해야 합니다 (정규식: ^[A-Z][A-Z0-9_]*$).",
                    file=sys.stderr,
                )
                sys.exit(1)

        # .env 존재 확인 — 없으면 새로 만들지 않는다 (시크릿 유실 방지).
        if not os.path.isfile(dotenv_path):
            print(
                f"오류: .env 파일을 찾을 수 없습니다 ({dotenv_path}). "
                "시크릿이 포함된 .env 파일이 레포 루트에 있어야 합니다.",
                file=sys.stderr,
            )
            sys.exit(1)

        # 원본 백업 후 패치 적용.
        _original_dotenv = _patch_dotenv(dotenv_path, set_overrides)
        # 부모 프로세스 환경변수에도 동일 값을 반영한다.
        # _drain_topics 는 os.environ 의 TOPIC_MIN_NET_ARTICLE_COUNT 로 잔량을 세므로,
        # .env 만 패치하면 자식(패치값)과 부모(기존값)의 자격 기준이 어긋나
        # 드레인이 조기 종료되어 대상 이벤트가 미배정으로 남을 수 있다.
        for kv in set_overrides:
            key, value = kv.split("=", 1)
            os.environ[key] = value
        overridden_keys = [kv.split("=", 1)[0] for kv in set_overrides]
        print(
            f"[.env 임시 패치] 이번 실행에 적용된 키: {', '.join(overridden_keys)}\n"
            f"  실행 완료(또는 중단) 후 원본 .env 를 자동으로 복원합니다."
        )

    # config-tag 옆에 --set 키를 병기한다 (config-tag 값 자체는 변경하지 않는다).
    set_display = (
        "\n  --set 적용 : " + "  ".join(set_overrides)
        if set_overrides else ""
    )
    print(
        f"=== 평가 반복 시작 ===\n"
        f"  run-id    : {args.run_id}\n"
        f"  config-tag: {args.config_tag}"
        f"{set_display}\n"
        f"  articles  : {args.articles}\n"
        f"  gold      : {args.gold}"
    )

    try:
        if args.topics_only:
            # ── --topics-only: 이벤트 레이어 고정, 토픽 레이어만 재실행 ─────────
            # DB 초기화·기사 주입·이벤트 드레인 단계를 건너뛰고,
            # topic_id·topic_causes·topics 만 초기화한 뒤 이어서 진행한다.
            _topic_only_reset(db_url)
        else:
            # ── 단계 1: DB 초기화 ─────────────────────────────────────────────
            _run_step(
                "DB 초기화 (reset_test_db)",
                [py, os.path.join(_EVAL_DIR, "reset_test_db.py"),
                 "--database-url", db_url, "--yes"],
                reset_env,
            )

            # ── 단계 2: 더미 기사 주입 ────────────────────────────────────────
            _run_step(
                "더미 기사 주입 (ingest_dummy)",
                [py, os.path.join(_EVAL_DIR, "ingest_dummy.py"),
                 "--database-url", db_url, "--articles", args.articles],
                base_env,
            )

            # ── 단계 3: 이벤트 드레인 루프 ────────────────────────────────────
            # .env 의 load_dotenv(override=True) 가 자식 환경변수를 덮어쓰므로 배치 크기
            # 주입 방식은 동작하지 않는다. status='done' 잔량이 0 이 될 때까지 반복 호출한다.
            _drain_events(
                db_url,
                [py, os.path.join(_REPO_ROOT, "classify_events.py"), "--database-url", db_url],
                events_env,
                n_articles,
            )

        # ── 단계 4.5: article_count 재동기화 (인-프로세스) ───────────────────
        _resync_article_counts(db_url)

        # ── 단계 5: 토픽 드레인 루프 ─────────────────────────────────────────
        # 서브토픽 활성화 env 를 유지하면서 미배정 이벤트가 0 이 될 때까지 반복 호출한다.
        _drain_topics(
            db_url,
            [py, os.path.join(_REPO_ROOT, "classify_topics.py"), "--database-url", db_url],
            topics_env,
            n_articles,
        )

        # ── 단계 6: 평가 지표 산출 ────────────────────────────────────────────
        _run_step(
            "평가 지표 산출 (evaluate)",
            [py, os.path.join(_EVAL_DIR, "evaluate.py"),
             "--database-url", db_url,
             "--gold", args.gold,
             "--run-id", args.run_id,
             "--config-tag", args.config_tag],
            base_env,
        )

        print(
            f"\n=== 평가 반복 완료: {args.run_id} ===\n"
            f"  리포트 : eval/results/{args.run_id}.md\n"
            f"  누적표 : eval/results/metrics.csv"
        )

    finally:
        # .env 복원: 파이프라인 성공·실패·KeyboardInterrupt 여부와 무관하게 반드시 실행된다.
        if _original_dotenv is not None:
            with open(dotenv_path, "w", encoding="utf-8") as _f:
                _f.write(_original_dotenv)
            print("[.env 복원 완료] 원본 .env 파일이 정확하게 복원되었습니다.")


if __name__ == "__main__":
    main()
