"""운영 Postgres 스키마 검증기 단위 테스트.

커버 대상(모두 DB 없이 FakeConn으로 결정론적 검증):
- storage._required_columns_for — 수집기/분류기 스키마 분리 및 합집합
- storage._validate_postgres_schema — 누락 테이블/컬럼 시 시작 시점 RuntimeError
- storage._validate_vector_dimensions — pgvector 차원 불일치 감지, 미지정 시 통과

핵심 회귀 보호: 분류기 진입점은 events/topics/topic_causes/event_articles와
pgvector 컬럼이 없으면 배치 처리 전에 명확히 실패해야 한다. 반대로 수집기 경로
(require_classifier_schema=False)는 분류기 테이블이 없어도 영향을 받지 않아야 한다.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from collector import storage
from collector.storage import (
    _required_columns_for,
    _validate_postgres_schema,
    _validate_vector_dimensions,
)


class _FakeConn:
    """information_schema 조회를 흉내내는 최소 커넥션.

    schema: {table_name: set(column_names)} — DB에 "존재하는" 테이블/컬럼.
    PostgresConnection.query 와 동일하게 행 리스트(dict)를 직접 반환한다.
    """

    def __init__(self, schema):
        self._schema = schema

    def query(self, sql, params=None):
        params = params or ()
        if "information_schema.tables" in sql:
            requested = params
            return [{"table_name": t} for t in requested if t in self._schema]
        if "information_schema.columns" in sql:
            (table,) = params
            return [{"column_name": c} for c in sorted(self._schema.get(table, set()))]
        raise AssertionError(f"unexpected SQL: {sql}")


class _FakeVectorConn:
    """pg_attribute의 atttypmod(=pgvector 차원) 조회를 흉내내는 커넥션.

    dims: {(table, column): typmod or None}. 키가 없으면 빈 결과(컬럼 없음)를 반환.
    """

    def __init__(self, dims):
        self._dims = dims

    def query(self, sql, params=None):
        table, column = params
        if (table, column) not in self._dims:
            return []
        return [{"typmod": self._dims[(table, column)]}]


def _full_schema():
    """수집기 + 분류기 필수 컬럼을 모두 갖춘 정상 스키마."""
    schema = {table: set(cols) for table, cols in storage.COLLECTOR_REQUIRED_COLUMNS.items()}
    for table, cols in storage.CLASSIFIER_REQUIRED_COLUMNS.items():
        schema.setdefault(table, set()).update(cols)
    return schema


class RequiredColumnsForTests(unittest.TestCase):
    def test_collector_scope_excludes_classifier_tables(self):
        required = _required_columns_for(False)
        self.assertEqual(set(required), set(storage.COLLECTOR_REQUIRED_COLUMNS))
        self.assertNotIn("events", required)
        self.assertNotIn("topics", required)
        self.assertNotIn("topic_causes", required)

    def test_classifier_scope_is_union(self):
        required = _required_columns_for(True)
        # 분류기 전용 테이블이 포함된다.
        for table in ("events", "event_articles", "topics", "topic_causes"):
            self.assertIn(table, required)
        # articles는 수집기 컬럼 + 분류기 전용 컬럼(embedding/core_content)의 합집합이다.
        self.assertIn("embedding", required["articles"])
        self.assertIn("core_content", required["articles"])
        self.assertIn("status", required["articles"])  # 수집기 컬럼 유지
        # 계층 분류용 컬럼이 포함된다.
        self.assertIn("parent_topic_id", required["topics"])
        self.assertIn("cause_embedding", required["topic_causes"])
        self.assertIn("prev_event_id", required["events"])
        self.assertIn("next_event_id", required["events"])


class ValidatePostgresSchemaTests(unittest.TestCase):
    def test_full_schema_passes_for_classifier(self):
        conn = _FakeConn(_full_schema())
        # 예외가 없어야 한다.
        _validate_postgres_schema(conn, _required_columns_for(True))

    def test_collector_path_unaffected_by_missing_classifier_tables(self):
        # events/topics 등이 전혀 없는 DB라도 수집기 검증은 통과해야 한다.
        collector_only = {
            table: set(cols)
            for table, cols in storage.COLLECTOR_REQUIRED_COLUMNS.items()
        }
        conn = _FakeConn(collector_only)
        _validate_postgres_schema(conn, _required_columns_for(False))

    def test_missing_parent_topic_id_column_fails(self):
        schema = _full_schema()
        schema["topics"].discard("parent_topic_id")
        conn = _FakeConn(schema)
        with self.assertRaises(RuntimeError) as ctx:
            _validate_postgres_schema(conn, _required_columns_for(True))
        msg = str(ctx.exception)
        self.assertIn("topics", msg)
        self.assertIn("parent_topic_id", msg)

    def test_missing_classifier_table_fails(self):
        schema = _full_schema()
        del schema["topic_causes"]
        conn = _FakeConn(schema)
        with self.assertRaises(RuntimeError) as ctx:
            _validate_postgres_schema(conn, _required_columns_for(True))
        self.assertIn("topic_causes", str(ctx.exception))

    def test_missing_postgres_only_event_column_fails(self):
        schema = _full_schema()
        schema["events"].discard("embedding_text")
        conn = _FakeConn(schema)
        with self.assertRaises(RuntimeError) as ctx:
            _validate_postgres_schema(conn, _required_columns_for(True))
        self.assertIn("embedding_text", str(ctx.exception))


class ValidateVectorDimensionsTests(unittest.TestCase):
    def test_correct_dimension_passes(self):
        dims = {key: 4096 for key in storage.EXPECTED_VECTOR_DIMENSIONS}
        _validate_vector_dimensions(_FakeVectorConn(dims))

    def test_wrong_dimension_fails(self):
        dims = {key: 4096 for key in storage.EXPECTED_VECTOR_DIMENSIONS}
        dims[("articles", "embedding")] = 768
        with self.assertRaises(RuntimeError) as ctx:
            _validate_vector_dimensions(_FakeVectorConn(dims))
        msg = str(ctx.exception)
        self.assertIn("articles.embedding", msg)
        self.assertIn("768", msg)

    def test_unspecified_dimension_is_skipped(self):
        # atttypmod -1(미지정)이나 컬럼 미존재 시 오탐으로 막지 않는다.
        dims = {key: -1 for key in storage.EXPECTED_VECTOR_DIMENSIONS}
        _validate_vector_dimensions(_FakeVectorConn(dims))
        _validate_vector_dimensions(_FakeVectorConn({}))


if __name__ == "__main__":
    unittest.main()
