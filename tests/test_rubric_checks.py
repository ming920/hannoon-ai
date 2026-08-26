"""eval/rubric_checks.py 유닛테스트.

순수 함수(제목 분류기·유사도)는 DB 없이 검증하고, DB 집계 함수는 SQLite
in-memory(ensure_db의 sqlite 경로)에 events/topics/event_articles 테이블을 수동으로
만들어 최소 1케이스를 검증한다(ensure_sqlite_db는 수집기 테이블만 자동 생성하고
분류기 테이블은 만들지 않으므로 — src/collector/storage.py 참고).

실행: python -m unittest tests.test_rubric_checks
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

# eval/ 디렉터리를 경로에 추가한다(rubric_checks.py 임포트용).
# rubric_checks.py 자체가 모듈 로드 시 src/를 경로에 추가하므로
# collector.storage 임포트도 자동으로 처리된다.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))

from rubric_checks import (  # noqa: E402
    check_similar_title_unassigned_event_pairs,
    classify_attribute_subtopic_title,
    classify_category_topic_title,
    classify_member_count,
    classify_time_based_subtopic_title,
    compute_topic_depth,
    find_similar_title_pairs,
    is_similar_title,
    run_all_checks,
    title_similarity,
    topics_has_parent_topic_id,
)

from collector.storage import ensure_db  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# 순수 함수 테스트
# ══════════════════════════════════════════════════════════════════════════


class TestClassifyMemberCount(unittest.TestCase):
    def test_zero_is_no_item(self):
        self.assertEqual(classify_member_count(0), "무항목")

    def test_one_is_single(self):
        self.assertEqual(classify_member_count(1), "단일")

    def test_multi(self):
        self.assertEqual(classify_member_count(2), "다중")
        self.assertEqual(classify_member_count(10), "다중")


class TestClassifyTimeBasedSubtopicTitle(unittest.TestCase):
    """R-S2: 시간 구분형 서브토픽 제목 분류기."""

    def test_positive_ordinal_day_with_space(self):
        self.assertTrue(classify_time_based_subtopic_title("G7 정상회의 첫째 날"))

    def test_positive_ordinal_day_no_space(self):
        self.assertTrue(classify_time_based_subtopic_title("지방선거 사전투표 둘째날"))

    def test_positive_nth_day_suffix(self):
        self.assertTrue(classify_time_based_subtopic_title("전공의 파업 30일차"))

    def test_positive_nth_jjae_suffix(self):
        self.assertTrue(classify_time_based_subtopic_title("사전투표 3일째"))

    def test_positive_nth_week_suffix(self):
        self.assertTrue(classify_time_based_subtopic_title("전공의 파업 3주차"))

    def test_positive_nth_cha_suffix(self):
        self.assertTrue(classify_time_based_subtopic_title("노사협상 5차 회의"))

    def test_negative_no_time_marker(self):
        self.assertFalse(classify_time_based_subtopic_title("전공의 집단 사직"))

    def test_negative_policy_topic(self):
        self.assertFalse(classify_time_based_subtopic_title("의대 정원 증원 확정"))

    def test_negative_assembly_vote(self):
        self.assertFalse(classify_time_based_subtopic_title("국회 탄핵소추안 표결"))

    def test_negative_crackdown(self):
        self.assertFalse(classify_time_based_subtopic_title("무단횡단 단속 강화"))

    def test_negative_market_move(self):
        self.assertFalse(classify_time_based_subtopic_title("환율 급등락"))

    def test_negative_word_containing_cha_without_boundary(self):
        """'차'가 들어가더라도 숫자+차 뒤에 경계가 없으면(예: 3차원) 매치하지 않는다."""
        self.assertFalse(classify_time_based_subtopic_title("3차원 데이터 분석"))


class TestClassifyAttributeSubtopicTitle(unittest.TestCase):
    """R-S3: 속성형(찬반 등) 서브토픽 제목 분류기."""

    def test_positive_agree_with_space(self):
        self.assertTrue(classify_attribute_subtopic_title("의대 증원 찬성 여론"))

    def test_positive_disagree(self):
        self.assertTrue(classify_attribute_subtopic_title("정책 반대 진영"))

    def test_positive_positive_eval(self):
        self.assertTrue(classify_attribute_subtopic_title("감세안 긍정 평가"))

    def test_positive_advocate_suffix(self):
        self.assertTrue(classify_attribute_subtopic_title("이민정책 옹호론"))

    def test_positive_critic_with_space(self):
        self.assertTrue(classify_attribute_subtopic_title("여당 정책 비판 여론"))

    def test_positive_negative_jeok_suffix(self):
        self.assertTrue(classify_attribute_subtopic_title("국제유가 부정적 전망"))

    def test_negative_compound_noun_not_flagged(self):
        """'부정선거'처럼 속성 키워드가 복합명사로 붙은 경우는 매치하지 않는다."""
        self.assertFalse(classify_attribute_subtopic_title("부정선거 의혹 수사"))

    def test_negative_no_keyword(self):
        self.assertFalse(classify_attribute_subtopic_title("전공의 집단 사직"))

    def test_negative_medical_reform(self):
        self.assertFalse(classify_attribute_subtopic_title("의료개혁 전공의 파업"))

    def test_negative_hearing(self):
        self.assertFalse(classify_attribute_subtopic_title("국정감사 여야 공방"))

    def test_negative_flood_damage(self):
        self.assertFalse(classify_attribute_subtopic_title("장마철 침수 피해"))


class TestClassifyCategoryTopicTitle(unittest.TestCase):
    """R-T2: 카테고리형 토픽 제목 분류기."""

    def test_positive_bare_category(self):
        self.assertTrue(classify_category_topic_title("정치"))

    def test_positive_category_with_suffix(self):
        self.assertTrue(classify_category_topic_title("경제 뉴스"))

    def test_positive_society(self):
        self.assertTrue(classify_category_topic_title("사회"))

    def test_positive_international_issue(self):
        self.assertTrue(classify_category_topic_title("국제 이슈"))

    def test_positive_sports(self):
        self.assertTrue(classify_category_topic_title("스포츠"))

    def test_negative_specific_topic(self):
        self.assertFalse(classify_category_topic_title("의료개혁"))

    def test_negative_election(self):
        self.assertFalse(classify_category_topic_title("2026년 6.3 지방선거"))

    def test_negative_starbucks_controversy(self):
        self.assertFalse(classify_category_topic_title("스타벅스 탱크데이 논란"))

    def test_negative_homeplus(self):
        self.assertFalse(classify_category_topic_title("홈플러스 법인회생 절차 신청"))

    def test_negative_category_word_with_extra_context(self):
        """카테고리명이 포함되어도 단독 제목이 아니면(수식어가 붙으면) 매치하지 않는다."""
        self.assertFalse(classify_category_topic_title("정치권 통상 갈등"))


class TestTitleSimilarity(unittest.TestCase):
    def test_identical_titles_ratio_one(self):
        self.assertAlmostEqual(title_similarity("의료개혁", "의료개혁"), 1.0)

    def test_unrelated_titles_low_ratio(self):
        self.assertLess(title_similarity("의료개혁", "스타벅스 탱크데이 논란"), 0.5)

    def test_is_similar_title_threshold(self):
        self.assertTrue(is_similar_title("의료개혁 국면", "의료개혁 국면 확산", 0.8))
        self.assertFalse(is_similar_title("의료개혁", "스타벅스 탱크데이 논란", 0.8))

    def test_none_inputs_do_not_crash(self):
        self.assertEqual(title_similarity(None, None), 1.0)
        self.assertFalse(is_similar_title(None, "제목", 0.8))


class TestFindSimilarTitlePairs(unittest.TestCase):
    def test_finds_pair_above_threshold(self):
        items = [
            (1, "정부 의대 증원 발표"),
            (2, "정부 의대 증원 발표 재송고"),
            (3, "전공의 집단 사직 확산"),
        ]
        pairs = find_similar_title_pairs(items, 0.8)
        self.assertEqual(len(pairs), 1)
        self.assertEqual((pairs[0][0], pairs[0][2]), (1, 2))

    def test_no_pairs_below_threshold(self):
        items = [(1, "의료개혁"), (2, "스타벅스 탱크데이 논란")]
        self.assertEqual(find_similar_title_pairs(items, 0.8), [])

    def test_empty_and_singleton_do_not_crash(self):
        self.assertEqual(find_similar_title_pairs([], 0.8), [])
        self.assertEqual(find_similar_title_pairs([(1, "제목")], 0.8), [])


class TestComputeTopicDepth(unittest.TestCase):
    def test_root_depth_one(self):
        parent_map = {1: None}
        self.assertEqual(compute_topic_depth(1, parent_map), 1)

    def test_leaf_depth_two(self):
        parent_map = {1: None, 2: 1}
        self.assertEqual(compute_topic_depth(2, parent_map), 2)

    def test_three_level_depth_three(self):
        parent_map = {1: None, 2: 1, 3: 2}
        self.assertEqual(compute_topic_depth(3, parent_map), 3)

    def test_cycle_does_not_infinite_loop(self):
        """순환 참조(비정상 데이터)가 있어도 무한루프 없이 종료해야 한다."""
        parent_map = {1: 2, 2: 1}
        depth = compute_topic_depth(1, parent_map)
        self.assertIsInstance(depth, int)


# ══════════════════════════════════════════════════════════════════════════
# DB 집계 스모크 테스트 (SQLite in-memory fixture)
# ══════════════════════════════════════════════════════════════════════════


def _build_fixture_conn():
    """events/topics/event_articles를 갖춘 SQLite in-memory 연결을 만든다.

    ensure_sqlite_db는 수집기 테이블(feeds/articles/article_jobs/article_ai_results)만
    자동 생성하므로, 분류기 테이블은 여기서 직접 만든다.
    """
    conn = ensure_db(":memory:")
    conn.execute(
        """
        CREATE TABLE topics (
            id INTEGER PRIMARY KEY,
            title TEXT,
            parent_topic_id INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            title TEXT,
            topic_id INTEGER,
            category TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE event_articles (
            event_id INTEGER,
            article_id INTEGER
        )
        """
    )

    topics = [
        (1, "정치", None),                       # R-T2 위반 (카테고리형)
        (2, "의료개혁", None),                    # root, 자식 5/6/7/8 보유
        (4, "의료개혁", None),                    # R-T1 위반 (2와 제목 완전 동일)
        (5, "전공의 집단 사직", 2),
        (6, "의대 증원 반대 여론", 2),             # R-S3 위반 (속성형)
        (7, "의료개혁", 2),                        # R-S4 위반 (부모와 동일 범위)
        (8, "전공의 파업 3일차", 2),               # R-S2 위반 (시간 구분형)
        (9, "세부 갈래", 7),                       # R-S5 깊이 위반 (depth=3)
    ]
    for topic_id, title, parent_id in topics:
        conn.execute(
            "INSERT INTO topics (id, title, parent_topic_id) VALUES (?, ?, ?)",
            (topic_id, title, parent_id),
        )

    events = [
        (101, "정부 의대 증원 발표", 5),
        (102, "전공의 집단 사직 확산", 5),
        (103, "의대 증원 반대 집회", 6),           # 단일 이벤트 서브토픽(6)
        (104, "전공의 파업 3일차 상황", 8),         # 단일 이벤트 서브토픽(8)
        (105, "법원 집행정지 신청", 2),             # 비-leaf(2)에 직결 — R-S5 위반
        (106, "정부 의대 증원 발표 재송고", 5),      # 101과 유사 제목, 동일 토픽(5) — R-E2 위반
        (107, "단독 속보", None),                   # 토픽 미배정
    ]
    for event_id, title, topic_id in events:
        conn.execute(
            "INSERT INTO events (id, title, topic_id) VALUES (?, ?, ?)",
            (event_id, title, topic_id),
        )

    article_counts = {101: 1, 102: 2, 103: 1, 104: 1, 105: 3, 106: 1, 107: 0}
    article_id_seq = 1
    for event_id, count in article_counts.items():
        for _ in range(count):
            conn.execute(
                "INSERT INTO event_articles (event_id, article_id) VALUES (?, ?)",
                (event_id, article_id_seq),
            )
            article_id_seq += 1

    return conn


class TestRunAllChecksSmoke(unittest.TestCase):
    """SQLite fixture 하나로 9개 검사 ID 전부를 스모크 검증한다."""

    def setUp(self):
        self.conn = _build_fixture_conn()

    def tearDown(self):
        self.conn.close()

    def test_r_e1_single_article_event_ratio(self):
        r = run_all_checks(self.conn)["R-E1"]
        self.assertEqual(r["total"], 7)
        self.assertEqual(r["violations"], 4)  # 101,103,104,106
        self.assertAlmostEqual(r["ratio"], 4 / 7)
        self.assertEqual(r["zero_article_events"], 1)  # 107

    def test_r_e2_similar_title_event_pairs(self):
        r = run_all_checks(self.conn)["R-E2"]
        self.assertEqual(r["total_events_with_topic"], 6)
        self.assertEqual(r["violations"], 1)
        pair = r["examples"][0]
        self.assertEqual({pair["event_id_a"], pair["event_id_b"]}, {101, 106})

    def test_r_s1_single_event_subtopic_ratio(self):
        r = run_all_checks(self.conn)["R-S1"]
        self.assertEqual(r["total"], 5)  # 5,6,7,8,9
        self.assertEqual(r["violations"], 2)  # 6,8
        self.assertAlmostEqual(r["ratio"], 2 / 5)

    def test_r_s2_time_based_titles(self):
        r = run_all_checks(self.conn)["R-S2"]
        self.assertEqual(r["violations"], 1)
        self.assertEqual(r["examples"][0]["id"], 8)

    def test_r_s3_attribute_titles(self):
        r = run_all_checks(self.conn)["R-S3"]
        self.assertEqual(r["violations"], 1)
        self.assertEqual(r["examples"][0]["id"], 6)

    def test_r_s4_topic_scope_subtopics(self):
        r = run_all_checks(self.conn)["R-S4"]
        self.assertEqual(r["total"], 5)
        self.assertEqual(r["violations"], 1)
        self.assertEqual(r["examples"][0]["subtopic_id"], 7)

    def test_r_s5_hierarchy_consistency(self):
        r = run_all_checks(self.conn)["R-S5"]
        self.assertEqual(r["events_on_non_leaf_topic"], 1)
        self.assertEqual(r["events_on_non_leaf_topic_examples"][0]["event_id"], 105)
        self.assertEqual(r["topics_depth_exceeded"], 1)
        self.assertEqual(r["topics_depth_exceeded_examples"][0]["id"], 9)

    def test_r_t1_duplicate_topic_pairs(self):
        r = run_all_checks(self.conn)["R-T1"]
        self.assertEqual(r["total_root_topics"], 3)  # 1,2,4
        self.assertEqual(r["violations"], 1)
        self.assertEqual(r["exact_duplicate_pairs"], 1)

    def test_r_t2_category_titles(self):
        r = run_all_checks(self.conn)["R-T2"]
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["violations"], 1)
        self.assertEqual(r["examples"][0]["id"], 1)

    def test_observations_report_subtopic_row_count(self):
        with patch.dict(os.environ, {"TOPIC_SUBTOPICS_ENABLED": "true"}):
            obs = run_all_checks(self.conn)["observations"]
        self.assertTrue(obs["topic_subtopics_enabled_env"])
        self.assertEqual(obs["subtopic_row_count"], 5)  # 5,6,7,8,9


class TestUnassignedSimilarTitlePairs(unittest.TestCase):
    """R-E2U: 미배정(topic_id IS NULL) 이벤트 유사 제목 쌍."""

    def setUp(self):
        self.conn = ensure_db(":memory:")
        self.conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT, topic_id INTEGER, category TEXT)"
        )
        rows = [
            (1, "군 무인기 침투 사건 1심 선고", None, "정치"),
            (2, "군 무인기 침투 사건 1심 선고 공판", None, "정치"),  # 1과 유사 — 위반 쌍
            (3, "군 무인기 침투 사건 1심 선고", None, "국제"),  # 제목 동일하지만 다른 버킷 — 미검출(버킷 한계)
            (4, "일본은행 기준금리 동결", None, "경제"),
            (5, "전공의 복귀 협상 개시", 7, "사회"),  # 배정됨 — 모집단 제외
            (6, "카테고리 없음 단독 사건", None, None),  # 무분류 버킷
        ]
        for r in rows:
            self.conn.execute(
                "INSERT INTO events (id, title, topic_id, category) VALUES (?, ?, ?, ?)", r
            )

    def tearDown(self):
        self.conn.close()

    def test_pairs_detected_within_bucket(self):
        r = check_similar_title_unassigned_event_pairs(self.conn, threshold=0.8)
        self.assertEqual(r["total"], 5)  # topic_id NULL 5건
        self.assertEqual(r["violations"], 1)
        pair = r["examples"][0]
        self.assertEqual({pair["event_id_a"], pair["event_id_b"]}, {1, 2})
        self.assertEqual(pair["category"], "정치")


class TestPreSubtopicSchemaGuard(unittest.TestCase):
    """parent_topic_id 컬럼이 없는(서브토픽 마이그레이션 미적용) 스키마에서의 동작."""

    def setUp(self):
        self.conn = ensure_db(":memory:")
        self.conn.execute("CREATE TABLE topics (id INTEGER PRIMARY KEY, title TEXT)")
        self.conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT, topic_id INTEGER, category TEXT)"
        )
        self.conn.execute("CREATE TABLE event_articles (event_id INTEGER, article_id INTEGER)")
        self.conn.execute("INSERT INTO topics (id, title) VALUES (?, ?)", (1, "정치"))
        self.conn.execute("INSERT INTO topics (id, title) VALUES (?, ?)", (2, "의료개혁"))
        self.conn.execute(
            "INSERT INTO events (id, title, topic_id, category) VALUES (?, ?, ?, ?)",
            (101, "정부 의대 증원 발표", 2, "사회"),
        )
        self.conn.execute(
            "INSERT INTO event_articles (event_id, article_id) VALUES (?, ?)", (101, 1)
        )

    def tearDown(self):
        self.conn.close()

    def test_detects_missing_column(self):
        self.assertFalse(topics_has_parent_topic_id(self.conn))

    def test_subtopic_checks_skipped_and_rest_run(self):
        results = run_all_checks(self.conn)
        for check_id in ("R-S1", "R-S2", "R-S3", "R-S4", "R-S5"):
            self.assertTrue(results[check_id].get("skipped"), check_id)
        self.assertEqual(results["R-E1"]["total"], 1)
        self.assertEqual(results["R-T1"]["total_root_topics"], 2)  # 전체 토픽을 최상위로 간주
        self.assertEqual(results["R-T2"]["violations"], 1)  # "정치"
        self.assertIsNone(results["observations"]["subtopic_row_count"])


if __name__ == "__main__":
    unittest.main()
