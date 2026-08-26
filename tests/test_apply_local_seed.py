"""scripts/apply_local_seed.py 유닛테스트.

이 스크립트에서 데이터를 지울 수 있는 유일한 경로는 `--replace` 다. 그래서 "언제 거부하는가"를
DB 없이 고정한다 — 특히 **원격 DB 에서는 --allow-remote 를 줘도 --replace 가 통하면 안 된다.**
원격에 시드를 넣는 것과 원격 데이터를 지우는 것은 전혀 다른 일이다.

실행: python -m unittest tests.test_apply_local_seed
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from apply_local_seed import (  # noqa: E402
    CLEAR_STATEMENTS,
    CLEARED_TABLES,
    check_host_guards,
    check_state_guards,
    is_local_host,
    seed_targets_articles,
)

PROD_HOST = "aws-1-ap-northeast-2.pooler.supabase.com"


def host_guard(**kwargs) -> str | None:
    params = {"host": "127.0.0.1", "allow_remote": False, "replace": False}
    params.update(kwargs)
    return check_host_guards(**params)


def state_guard(**kwargs) -> str | None:
    params = {"seeds_articles": True, "existing_articles": 0, "replace": False}
    params.update(kwargs)
    return check_state_guards(**params)


class RemoteReplaceGuardTests(unittest.TestCase):
    """--replace 는 로컬 전용이어야 한다 (2026-08 코드리뷰 지적)."""

    def test_remote_replace_is_refused_even_with_allow_remote(self):
        reason = host_guard(host=PROD_HOST, allow_remote=True, replace=True)
        self.assertIsNotNone(reason, "원격에서 --replace 가 허용되면 프로덕션 데이터가 지워진다")
        self.assertIn("--replace", reason)
        self.assertIn("로컬", reason)

    def test_remote_without_allow_remote_is_refused(self):
        reason = host_guard(host=PROD_HOST)
        self.assertIsNotNone(reason)
        self.assertIn("--allow-remote", reason)

    def test_remote_insert_only_is_allowed_with_allow_remote(self):
        # 지우지 않고 넣기만 하는 것은 --allow-remote 로 허용된다.
        self.assertIsNone(host_guard(host=PROD_HOST, allow_remote=True))

    def test_local_replace_is_allowed(self):
        self.assertIsNone(host_guard(replace=True))

    def test_ipv6_localhost_counts_as_local(self):
        self.assertTrue(is_local_host("::1"))
        self.assertIsNone(host_guard(host="::1", replace=True))

    def test_host_guard_needs_no_database_state(self):
        """연결 전에 판단할 수 있어야 한다 — DB 상태를 인자로 받지 않는다."""
        import inspect

        params = set(inspect.signature(check_host_guards).parameters)
        self.assertEqual(params, {"host", "allow_remote", "replace"})


class ArticleStateGuardTests(unittest.TestCase):
    def test_article_seed_refuses_non_empty_without_replace(self):
        reason = state_guard(existing_articles=9)
        self.assertIsNotNone(reason)
        self.assertIn("--replace", reason)

    def test_article_seed_into_empty_table_is_allowed(self):
        self.assertIsNone(state_guard(existing_articles=0))

    def test_article_seed_with_replace_is_allowed(self):
        self.assertIsNone(state_guard(existing_articles=9, replace=True))

    def test_event_seed_requires_articles_to_exist(self):
        # 이벤트 시드는 기사를 참조하므로 articles 가 비어 있으면 FK 가 깨진다.
        reason = state_guard(seeds_articles=False, existing_articles=0)
        self.assertIsNotNone(reason)
        self.assertIn("비어", reason)

    def test_event_seed_with_articles_present_is_allowed(self):
        self.assertIsNone(state_guard(seeds_articles=False, existing_articles=2578))


class SeedDetectionTests(unittest.TestCase):
    def test_detects_unquoted_form(self):
        self.assertTrue(seed_targets_articles("INSERT INTO public.articles (id, guid) VALUES (1,'x');"))

    def test_detects_quoted_form(self):
        # 생성기(psycopg sql.Identifier)는 따옴표를 붙인다.
        self.assertTrue(seed_targets_articles('INSERT INTO public."articles" ("id") VALUES (1);'))

    def test_event_seed_is_not_article_seed(self):
        sql = (
            "-- 전제: articles_2026-07-20.sql 을 먼저 적용해야 한다\n"
            'INSERT INTO public."events" ("id","title") VALUES (1,\'x\');\n'
            'INSERT INTO public."event_articles" ("event_id","article_id") VALUES (1,2);\n'
        )
        self.assertFalse(
            seed_targets_articles(sql),
            "주석에 파일명이 언급된 것만으로 기사 시드로 오판하면 가드 방향이 뒤집힌다",
        )


class ClearStatementConsistencyTests(unittest.TestCase):
    """--replace 도움말이 실제 삭제 범위와 어긋나면 안 된다."""

    def test_every_advertised_table_is_actually_cleared(self):
        for table in CLEARED_TABLES:
            with self.subTest(table=table):
                self.assertTrue(
                    any(f"DELETE FROM public.{table}" == s for s in CLEAR_STATEMENTS),
                    f"{table} 을 지운다고 안내하면서 실제로 지우지 않는다",
                )

    def test_every_deleted_table_is_advertised(self):
        deleted = {
            s.removeprefix("DELETE FROM public.")
            for s in CLEAR_STATEMENTS
            if s.startswith("DELETE FROM public.")
        }
        self.assertEqual(
            deleted - set(CLEARED_TABLES),
            set(),
            "도움말에 없는 테이블을 지우고 있다 — 사용자가 예상하지 못한다",
        )

    def test_self_references_are_broken_before_delete(self):
        # events/topics 는 자기참조 FK 가 있어 NULL 로 끊지 않으면 DELETE 가 실패한다.
        order = list(CLEAR_STATEMENTS)
        for table, column in (("events", "prev_event_id"), ("topics", "parent_topic_id")):
            with self.subTest(table=table):
                null_idx = next(
                    i for i, s in enumerate(order) if s.startswith(f"UPDATE public.{table}") and column in s
                )
                del_idx = order.index(f"DELETE FROM public.{table}")
                self.assertLess(null_idx, del_idx, f"{table} 자기참조를 끊기 전에 삭제한다")


if __name__ == "__main__":
    unittest.main()
