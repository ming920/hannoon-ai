"""이벤트 벡터 갱신 방식(anchor / centroid) 설정과 SQL 계약 테스트.

기본 동작에서 이벤트 벡터는 create_new_event 가 심은 **첫 기사의 임베딩**에 고정된다.
갱신하는 함수가 아예 없었다. 그래서 "이벤트와의 거리"는 실제로는 "먼저 도착한 기사 한 건과의
거리"이고, 이벤트가 커질수록 대표성을 잃는다. 로컬 실측(기사 722건 / 이벤트 177개):

    이벤트 크기    구성원→앵커 평균   구성원→중심 평균
    3~5건            0.224            0.236
    6~14건           0.217            0.156
    15건 이상        0.267            0.149

centroid 모드는 후보 검색 결과를 통째로 바꾸는 변경이라 **기본값은 기존 동작**이어야 한다.
여기서는 그 기본값과, 실수하기 쉬운 SQL 계약(호출 순서·건드리지 않아야 할 열)을 지킨다.

실행: python -m unittest tests.test_event_embedding_update
"""
from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from db import events  # noqa: E402


class SettingsDefaultTests(unittest.TestCase):
    """기본값이 기존 동작(anchor)이어야 한다 — 잰 적 없는 변경을 조용히 켜면 안 된다."""

    def _reload(self, value) -> str:
        """EVENT_EMBEDDING_UPDATE 를 바꿔 settings 를 다시 읽고 **해결된 값**을 돌려준다.

        settings 는 import 시점에 load_dotenv(override=True) 로 .env 를 읽으므로,
        .env 에 이 키가 없을 때만 환경변수가 반영된다. 테스트는 그 전제를 확인한 뒤 돈다.

        모듈 객체를 돌려주면 안 된다 — 아래 finally 가 원상복구하며 같은 모듈 객체를 다시
        reload 하므로, 호출자가 값을 읽는 시점에는 이미 되돌려져 있다.
        """
        import event_classifier.settings as s
        old = os.environ.get("EVENT_EMBEDDING_UPDATE")
        if value is None:
            os.environ.pop("EVENT_EMBEDDING_UPDATE", None)
        else:
            os.environ["EVENT_EMBEDDING_UPDATE"] = value
        try:
            return importlib.reload(s).EMBEDDING_UPDATE_MODE
        finally:
            if old is None:
                os.environ.pop("EVENT_EMBEDDING_UPDATE", None)
            else:
                os.environ["EVENT_EMBEDDING_UPDATE"] = old
            importlib.reload(s)

    @staticmethod
    def _dotenv_overrides() -> bool:
        env_file = ROOT / ".env"
        if not env_file.exists():
            return False
        return any(
            line.startswith("EVENT_EMBEDDING_UPDATE=")
            for line in env_file.read_text(encoding="utf-8").splitlines()
        )

    def setUp(self):
        if self._dotenv_overrides():
            self.skipTest(".env 가 EVENT_EMBEDDING_UPDATE 를 지정하고 있어 건너뜁니다")

    def test_default_is_anchor(self):
        self.assertEqual(self._reload(None), "anchor")

    def test_centroid_is_accepted(self):
        self.assertEqual(self._reload("centroid"), "centroid")

    def test_case_and_whitespace_tolerated(self):
        self.assertEqual(self._reload("  Centroid \n"), "centroid")

    def test_empty_falls_back_to_anchor(self):
        self.assertEqual(self._reload(""), "anchor")

    def test_unknown_value_fails_loudly(self):
        # 오타를 조용히 anchor 로 삼키면 "켰다고 생각했는데 안 켜진" 실험을 하게 된다.
        with self.assertRaises(ValueError) as ctx:
            self._reload("centriod")
        self.assertIn("centriod", str(ctx.exception))


class RecenterSqlContractTests(unittest.TestCase):
    """SQL 이 지켜야 할 계약. 어기면 조용히 틀린 벡터가 저장된다."""

    SQL = events.RECENTER_EVENT_EMBEDDING_SQL

    def test_updates_embedding_only(self):
        self.assertIn("SET embedding", self.SQL)
        # embedding_text 는 토픽 분류가 cause/result 를 뽑는 텍스트 입력이라 용도가 다르다.
        self.assertNotIn("embedding_text", self.SQL)

    def test_does_not_touch_updated_at(self):
        # 같은 트랜잭션의 update_event_summary 가 이미 갱신한다. 여기서 또 만지면
        # 후보검색 시간 윈도우에 의도치 않은 영향을 준다.
        self.assertNotIn("updated_at", self.SQL)

    def test_skips_articles_without_embedding(self):
        self.assertIn("a.embedding IS NOT NULL", self.SQL)

    def test_no_op_when_centroid_is_null(self):
        # 임베딩 있는 구성원이 하나도 없으면 벡터를 NULL 로 밀어버리면 안 된다.
        self.assertIn("sub.centroid IS NOT NULL", self.SQL)

    def test_uses_question_mark_placeholders(self):
        # 레포 규약: SQL 은 항상 ? 로 쓴다 (Postgres 래퍼가 %s 로 바꾼다).
        self.assertNotIn("%s", self.SQL)
        self.assertEqual(self.SQL.count("?"), 2)


class RecenterCallTests(unittest.TestCase):
    """호출 순서 — link 뒤에 불러야 방금 붙은 기사가 중심에 들어간다."""

    class _Recorder:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params=None):
            self.calls.append((sql, params))

    def test_passes_event_id_twice(self):
        conn = self._Recorder()
        events.recenter_event_embedding(conn, 42)
        (sql, params), = conn.calls
        self.assertIs(sql, events.RECENTER_EVENT_EMBEDDING_SQL)
        self.assertEqual(params, (42, 42))

    def test_pipeline_recenters_after_linking(self):
        # 순서가 뒤집히면 새 기사가 빠진 중심이 저장된다 — 조용히 틀린다.
        source = (SRC / "event_classifier" / "pipeline.py").read_text(encoding="utf-8")
        link = source.index("events.link_article_to_event")
        recenter = source.index("events.recenter_event_embedding")
        self.assertLess(link, recenter)

    def test_pipeline_guards_on_mode(self):
        source = (SRC / "event_classifier" / "pipeline.py").read_text(encoding="utf-8")
        guard = source.index("EMBEDDING_UPDATE_MODE == EMBEDDING_UPDATE_CENTROID")
        self.assertLess(guard, source.index("events.recenter_event_embedding"))


if __name__ == "__main__":
    unittest.main()
