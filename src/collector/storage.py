import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from summary_utils import normalize_summary

# DB 연결은 SQLite/Postgres 모두 autocommit으로 둔다(아래 ensure_* 참고).
# 따라서 단발 쓰기는 즉시 커밋되고, 원자적으로 묶어야 하는 다중 쓰기만
# `with conn.transaction():` 블록으로 감싼다. transaction() 중첩은 금지한다
# (sqlite는 중첩 BEGIN 불가, psycopg는 savepoint라 종료해도 커밋되지 않는다).

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - optional dependency for local SQLite use
    psycopg = None
    dict_row = None

try:
    from dateutil import parser as date_parser
except Exception:  # pragma: no cover - optional dependency
    date_parser = None


class PostgresCursor:
    """SQLite와 비슷하게 사용할 수 있도록 psycopg 커서를 감싸는 래퍼.

    기존 수집 코드는 SQLite의 `?` placeholder와 `lastrowid`에 맞춰 작성되어 있다.
    Postgres에서는 placeholder가 `%s`이고 INSERT 후 생성 ID를 `RETURNING id`로
    받아야 하므로, 이 클래스가 그 차이를 흡수한다.
    """

    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid: int | None = None

    def execute(self, sql: str, params: tuple | list | None = None):
        """SQLite 스타일 SQL을 Postgres 스타일로 변환해 실행한다."""
        sql = _to_postgres_sql(sql)
        if _is_article_insert(sql):
            sql = f"{sql.rstrip()} RETURNING id"
            self._cursor.execute(sql, params or ())
            row = self._cursor.fetchone()
            # 연결에 dict_row를 걸어두므로 RETURNING 결과도 dict로 들어온다.
            self.lastrowid = row["id"] if row else None
            return self
        self._cursor.execute(sql, params or ())
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self) -> None:
        self._cursor.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


class PostgresConnection:
    """SQLite Connection과 비슷한 최소 인터페이스를 제공하는 Postgres 연결 래퍼."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self) -> PostgresCursor:
        return PostgresCursor(self._conn.cursor())

    def execute(self, sql: str, params: tuple | list | None = None):
        """쓰기(INSERT/UPDATE/DELETE)나 cursor 직접 제어용. cursor를 반환."""
        return self.cursor().execute(sql, params)

    def query(self, sql: str, params: tuple | list | None = None) -> list:
        """SELECT → 결과 행 리스트(dict) 반환."""
        sql = _to_postgres_sql(sql)
        with self._conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()

    def query_one(self, sql: str, params: tuple | list | None = None):
        """SELECT → 첫 행만 반환 (없으면 None)."""
        sql = _to_postgres_sql(sql)
        with self._conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()

    def transaction(self):
        """여러 쓰기를 하나로 묶을 때 사용.
        블록 정상 종료 시 commit, 예외 시 rollback (psycopg 위임)."""
        return self._conn.transaction()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


class SqliteConnection:
    """SQLite 연결에도 Postgres 래퍼와 같은 query/query_one/transaction API를 제공한다.

    기존 수집 코드는 `cursor()`/`execute()`/`commit()`과 `cursor.lastrowid`를 그대로 쓰고,
    팀의 새 코드는 로컬·운영에서 동일한 query/query_one/transaction을 쓸 수 있게 한다.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def cursor(self) -> sqlite3.Cursor:
        # 원본 sqlite3 커서를 그대로 노출해 lastrowid 등 기존 동작을 유지한다.
        return self._conn.cursor()

    def execute(self, sql: str, params: tuple | list | None = None) -> sqlite3.Cursor:
        return self._conn.execute(sql, params or ())

    def query(self, sql: str, params: tuple | list | None = None) -> list:
        """SELECT → 결과 행 리스트(sqlite3.Row) 반환."""
        cur = self._conn.execute(sql, params or ())
        try:
            return cur.fetchall()
        finally:
            cur.close()

    def query_one(self, sql: str, params: tuple | list | None = None):
        """SELECT → 첫 행만 반환 (없으면 None)."""
        cur = self._conn.execute(sql, params or ())
        try:
            return cur.fetchone()
        finally:
            cur.close()

    @contextmanager
    def transaction(self):
        """여러 쓰기를 하나로 묶을 때 사용.
        연결이 autocommit(isolation_level=None)이라 명시적으로 BEGIN/COMMIT을 발행해야
        그룹 원자성이 생긴다. 정상 종료 시 commit, 예외 시 rollback 후 예외를 다시 던진다."""
        self._conn.execute("BEGIN")
        try:
            yield self
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def ensure_db(
    db_path: str,
    database_url: str | None = None,
    *,
    require_classifier_schema: bool = False,
):
    """실행 환경에 맞는 DB 연결을 만든다.

    - `database_url`이 있으면 Supabase/Postgres를 운영 DB로 사용한다.
      운영 DB 스키마는 별도 migration 흐름에서 관리하므로
      애플리케이션 시작 시 CREATE/ALTER TABLE을 실행하지 않는다.
    - 없으면 SQLite 파일을 사용한다. 이 경로는 로컬 개발과 테스트용이다.

    `require_classifier_schema=True`면 이벤트/토픽 분류기가 의존하는 테이블·컬럼
    (events/topics/topic_causes/event_articles + pgvector 컬럼)까지 시작 시점에 검증한다.
    분류기 진입점(classify_events.py / classify_topics.py)이 이 플래그를 켠다.
    """
    if database_url:
        return ensure_postgres_db(
            database_url, require_classifier_schema=require_classifier_schema
        )
    return ensure_sqlite_db(db_path)


def ensure_sqlite_db(db_path: str) -> "SqliteConnection":
    """SQLite DB와 필요한 테이블을 준비한다."""
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    # isolation_level=None으로 autocommit. 단발 쓰기는 즉시 커밋되고, 다중 쓰기는
    # SqliteConnection.transaction()의 명시적 BEGIN/COMMIT으로 묶는다(Postgres와 의미 일치).
    conn = sqlite3.connect(db_path, isolation_level=None)
    # 컬럼명 접근(row["name"])을 지원하면서도 인덱스 접근(row[0])과 호환된다.
    conn.row_factory = sqlite3.Row
    # 수집 중 읽기/쓰기가 겹쳐도 잠금 충돌을 줄이기 위해 WAL 모드를 사용한다.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feeds (
            url TEXT PRIMARY KEY,
            category TEXT,
            publisher TEXT,
            bias_type TEXT,
            title TEXT,
            etag TEXT,
            modified_at TEXT,
            last_checked TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_url TEXT,
            guid TEXT,
            link TEXT,
            category TEXT,
            title TEXT,
            publisher TEXT,
            bias_type TEXT,
            published_at TEXT,
            summary TEXT,
            content TEXT,
            article_image_url TEXT,
            content_source TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            -- RSS 피드마다 GUID 품질이 달라 link와 guid를 둘 다 중복 방지 키로 둔다.
            UNIQUE(link),
            UNIQUE(guid)
        )
        """
    )
    _ensure_column(conn, "feeds", "category", "TEXT")
    _ensure_column(conn, "feeds", "publisher", "TEXT")
    _ensure_column(conn, "feeds", "bias_type", "TEXT")
    _ensure_column(conn, "feeds", "modified_at", "TEXT")
    _ensure_column(conn, "articles", "category", "TEXT")
    _ensure_column(conn, "articles", "publisher", "TEXT")
    _ensure_column(conn, "articles", "bias_type", "TEXT")
    _ensure_column(conn, "articles", "published_at", "TEXT")
    _ensure_column(conn, "articles", "article_image_url", "TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS article_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER UNIQUE REFERENCES articles(id) ON DELETE CASCADE,
            status TEXT,
            attempts INTEGER,
            last_error TEXT,
            last_attempt_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS article_ai_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER UNIQUE REFERENCES articles(id) ON DELETE CASCADE,
            summary TEXT,
            keywords TEXT,
            status TEXT,
            last_error TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    return SqliteConnection(conn)


def ensure_postgres_db(
    database_url: str,
    *,
    require_classifier_schema: bool = False,
) -> PostgresConnection:
    """Supabase/Postgres DB에 연결하고 migration 적용 여부만 확인한다.

    Supabase 운영 스키마는 이 수집기 코드에서 만들지 않는다. 여기서 DDL을 실행하면
    운영 DB migration 히스토리와 애플리케이션 코드 변경 이력이 어긋날 수 있으므로,
    앱은 테이블을 만들지 않고 필요한 테이블/컬럼이 없을 때 명확히 실패한다.

    `require_classifier_schema=True`면 분류기 전용 테이블·컬럼과 pgvector 차원까지 검증한다.
    """
    if psycopg is None:
        raise RuntimeError("psycopg is required for Supabase/Postgres. Install requirements.txt first.")

    # dict_row를 걸면 하위 커서까지 상속되어 결과를 컬럼명으로 접근할 수 있다.
    # autocommit=True면 SELECT 후 즉시 IDLE로 돌아가, 이어지는 transaction() 블록이
    # 항상 트랜잭션을 소유(BEGIN/COMMIT)한다. 단발 쓰기는 즉시 커밋된다.
    conn = PostgresConnection(psycopg.connect(database_url, row_factory=dict_row, autocommit=True))
    try:
        _validate_postgres_schema(conn, _required_columns_for(require_classifier_schema))
        if require_classifier_schema:
            _validate_vector_dimensions(conn)
        return conn
    except Exception:
        conn.close()
        raise


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    """기존 SQLite DB에 새 컬럼이 없으면 추가한다."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


# 운영 Postgres에 있어야 하는 테이블별 컬럼.
# 수집기 경로(SQLite도 지원)가 요구하는 최소 스키마.
COLLECTOR_REQUIRED_COLUMNS = {
    "feeds": {
        "url",
        "category",
        "publisher",
        "bias_type",
        "title",
        "etag",
        "modified_at",
        "last_checked",
    },
    "articles": {
        "id",
        "feed_url",
        "guid",
        "link",
        "category",
        "title",
        "publisher",
        "bias_type",
        "published_at",
        "summary",
        "content",
        "article_image_url",
        "content_source",
        "status",
        "created_at",
        "updated_at",
    },
    "article_jobs": {
        "id",
        "article_id",
        "status",
        "attempts",
        "last_error",
        "last_attempt_at",
        "created_at",
        "updated_at",
    },
    "article_ai_results": {
        "id",
        "article_id",
        "summary",
        "keywords",
        "status",
        "last_error",
        "created_at",
        "updated_at",
    },
}

# 이벤트/토픽 분류기가 추가로 요구하는 테이블·컬럼(Postgres + pgvector 전용).
# 이 컬럼들이 없으면 분류기가 배치 도중이 아니라 시작 시점에 명확히 실패해야 한다.
# (db.events / db.topics / db.topic_causes / event_classifier / topic_classifier가 의존)
CLASSIFIER_REQUIRED_COLUMNS = {
    "articles": {"embedding", "core_content"},
    "events": {
        "id",
        "topic_id",
        "category",
        "title",
        "summary",
        "embedding_text",
        "core_content",
        "reason",
        "prev_event_id",
        "next_event_id",
        "article_count",
        "event_image_url",
        "embedding",
        "created_at",
        "updated_at",
    },
    "event_articles": {"id", "event_id", "article_id", "reason"},
    "topics": {
        "id",
        "category",
        "title",
        "summary",
        "parent_topic_id",
        "created_at",
        "updated_at",
    },
    "topic_causes": {"id", "topic_id", "cause_text", "cause_embedding"},
}

# 분류기 경로에서 차원까지 확인할 pgvector 컬럼. 과거 768↔4096 드리프트가 실제 사고였다.
EXPECTED_VECTOR_DIMENSIONS = {
    ("articles", "embedding"): 4096,
    ("events", "embedding"): 4096,
    ("topic_causes", "cause_embedding"): 4096,
}


def _required_columns_for(require_classifier_schema: bool) -> dict:
    """검증 대상 테이블→컬럼 집합을 구성한다.

    분류기 경로면 수집기 스키마에 분류기 스키마를 합집합으로 더한다.
    """
    merged = {table: set(columns) for table, columns in COLLECTOR_REQUIRED_COLUMNS.items()}
    if require_classifier_schema:
        for table, columns in CLASSIFIER_REQUIRED_COLUMNS.items():
            merged.setdefault(table, set()).update(columns)
    return merged


def _validate_vector_dimensions(conn: PostgresConnection) -> None:
    """pgvector 컬럼의 차원이 기대값과 같은지 best-effort로 확인한다.

    pgvector는 `atttypmod`에 차원을 그대로 저장한다(미지정이면 -1). 차원을 읽지 못하면
    조용히 건너뛰어 오탐으로 시작을 막지 않고, 양성으로 다른 차원이 확인될 때만 실패한다.
    """
    for (table, column), expected_dim in EXPECTED_VECTOR_DIMENSIONS.items():
        rows = conn.query(
            """
            SELECT a.atttypmod AS typmod
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = ?
              AND a.attname = ?
              AND a.attnum > 0
              AND NOT a.attisdropped
            """,
            (table, column),
        )
        if not rows:
            continue
        dim = rows[0]["typmod"]
        if dim is None or dim < 0:
            continue
        if dim != expected_dim:
            raise RuntimeError(
                f"Supabase/Postgres column '{table}.{column}' has vector dimension {dim}, "
                f"expected {expected_dim}. Re-apply the embedding-dimension migration "
                "and regenerate embeddings before running the classifier."
            )


def _validate_postgres_schema(conn: PostgresConnection, required_columns: dict) -> None:
    """운영 DB에 필요한 migration이 적용됐는지 DDL 없이 검증한다."""
    table_names = tuple(required_columns)
    table_placeholders = ", ".join("?" for _ in table_names)
    existing_tables = {
        row["table_name"]
        for row in conn.query(
            f"""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ({table_placeholders})
            """,
            table_names,
        )
    }
    missing_tables = sorted(set(table_names) - existing_tables)
    if missing_tables:
        raise RuntimeError(
            "Supabase/Postgres schema is missing tables: "
            + ", ".join(missing_tables)
            + ". Prepare the production schema before running the app."
        )

    for table, columns in required_columns.items():
        existing_columns = {
            row["column_name"]
            for row in conn.query(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ?
                """,
                (table,),
            )
        }
        missing_columns = sorted(columns - existing_columns)
        if missing_columns:
            raise RuntimeError(
                f"Supabase/Postgres table '{table}' is missing columns: "
                + ", ".join(missing_columns)
                + ". Prepare the production schema before running the app."
            )


def _to_postgres_sql(sql: str) -> str:
    """SQLite placeholder를 Postgres placeholder로 바꾼다."""
    return sql.replace("?", "%s")


def _is_article_insert(sql: str) -> bool:
    """articles INSERT는 생성된 article id가 필요하므로 별도 처리한다."""
    return sql.lstrip().upper().startswith("INSERT INTO ARTICLES")


def backfill_article_categories(conn) -> None:
    """이미 저장된 기사 중 category가 비어 있는 행을 피드 기준으로 보정한다."""
    # 피드 메타데이터가 채워진 뒤 기존 기사에도 같은 값을 반영한다.
    # 세 컬럼 보정을 한 트랜잭션으로 묶어 일부만 반영되는 상태를 막는다.
    with conn.transaction():
        conn.execute(
            """
            UPDATE articles
            SET category = (
                SELECT feeds.category
                FROM feeds
                WHERE feeds.url = articles.feed_url
            )
            WHERE category IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM feeds
                  WHERE feeds.url = articles.feed_url
                    AND feeds.category IS NOT NULL
              )
            """
        )
        conn.execute(
            """
            UPDATE articles
            SET publisher = (
                SELECT feeds.publisher
                FROM feeds
                WHERE feeds.url = articles.feed_url
            )
            WHERE publisher IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM feeds
                  WHERE feeds.url = articles.feed_url
                    AND feeds.publisher IS NOT NULL
              )
            """
        )
        conn.execute(
            """
            UPDATE articles
            SET bias_type = (
                SELECT feeds.bias_type
                FROM feeds
                WHERE feeds.url = articles.feed_url
            )
            WHERE bias_type IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM feeds
                  WHERE feeds.url = articles.feed_url
                    AND feeds.bias_type IS NOT NULL
              )
            """
        )


def now_iso() -> str:
    """DB에 저장할 UTC ISO timestamp를 만든다."""
    # 외부 시스템과 주고받기 쉬운 UTC ISO 문자열로 저장한다.
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def normalize_published(value: str | None) -> str | None:
    """RSS별로 제각각인 published/updated 값을 가능한 경우 ISO 문자열로 표준화한다."""
    if not value:
        return None
    if date_parser:
        try:
            # 피드마다 날짜 형식이 달라 dateutil로 가능한 범위만 표준화한다.
            return date_parser.parse(value).isoformat()
        except Exception:
            return value
    return value


def enqueue_article_job(conn, article_id: int) -> None:
    """후속 AI 처리 대상 기사를 article_jobs에 넣는다.

    `article_id`에 UNIQUE 제약이 있으므로 같은 기사가 RSS 단계와 crawl 단계에서
    여러 번 ready가 되어도 큐에는 한 번만 들어간다.

    자체 commit/transaction을 두지 않는 execute-only 헬퍼다. 기사 상태 UPDATE와
    원자적으로 묶이도록 호출부의 `with conn.transaction():` 블록 안에서 호출한다.
    """
    now = now_iso()
    # 같은 기사가 여러 경로로 ready가 되어도 후속 처리 작업에는 한 번만 들어간다.
    conn.execute(
        """
        INSERT INTO article_jobs (
            article_id, status, attempts, last_error, last_attempt_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_id) DO NOTHING
        """,
        (article_id, "pending", 0, None, None, now, now),
    )


def load_pending_ai_pipeline_jobs(conn, limit: int) -> list:
    """요약을 기사 단위로 이어서 처리할 ready 기사를 가져온다."""
    if limit <= 0:
        raise ValueError("limit must be greater than 0.")
    return conn.query(
        """
        SELECT
            article_jobs.id AS job_id,
            article_jobs.article_id AS article_id,
            article_jobs.attempts AS attempts,
            articles.title AS title,
            articles.summary AS rss_summary,
            articles.category AS category,
            articles.content AS content,
            articles.link AS link,
            article_ai_results.summary AS ai_summary
        FROM article_jobs
        JOIN articles ON articles.id = article_jobs.article_id
        LEFT JOIN article_ai_results
          ON article_ai_results.article_id = article_jobs.article_id
        WHERE article_jobs.status = ?
          AND articles.status = ?
          AND articles.content IS NOT NULL
        ORDER BY article_jobs.created_at ASC, article_jobs.id ASC
        LIMIT ?
        """,
        ("pending", "ready", limit),
    )


def save_article_analysis_result(
    conn,
    *,
    article_id: int,
    summary: str,
    keywords: list[str] | str | None,
    status: str = "done",
) -> None:
    """LLM 기사 분석 결과를 article_ai_results에 저장한다."""
    now = now_iso()
    summary = normalize_summary(summary)
    if not summary:
        raise ValueError("Article analysis summary is empty.")
    stored_keywords = _adapt_keywords_for_storage(conn, keywords)
    conn.execute(
        """
        INSERT INTO article_ai_results (
            article_id,
            summary,
            keywords,
            status,
            last_error,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            summary = excluded.summary,
            keywords = excluded.keywords,
            status = excluded.status,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        (
            article_id,
            summary,
            stored_keywords,
            status,
            None,
            now,
            now,
        ),
    )


def _adapt_keywords_for_storage(conn, keywords: list[str] | str | None):
    """Store keywords as text[] in Postgres and JSON text in local SQLite."""
    if keywords is None:
        return None

    if isinstance(keywords, str):
        try:
            parsed = json.loads(keywords)
        except json.JSONDecodeError:
            parsed = [keywords] if keywords.strip() else []
    else:
        parsed = keywords

    if isinstance(parsed, list):
        normalized = [
            " ".join(str(item).split())
            for item in parsed
            if " ".join(str(item).split())
        ]
    else:
        normalized = []

    if isinstance(conn, PostgresConnection):
        return normalized
    return json.dumps(normalized, ensure_ascii=False)


def mark_article_job_sent(conn, job_id: int) -> None:
    """후속 AI 처리가 끝난 기사 작업을 완료 상태로 표시한다."""
    if isinstance(conn, PostgresConnection):
        now = now_iso()
        conn.execute(
            """
            UPDATE article_jobs
            SET status = ?::public.article_job_status,
                last_error = ?,
                last_attempt_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            ("sent", None, now, now, job_id),
        )
        return

    now = now_iso()
    conn.execute(
        """
        UPDATE article_jobs
        SET status = ?, last_error = ?, last_attempt_at = ?, updated_at = ?
        WHERE id = ?
        """,
        ("sent", None, now, now, job_id),
    )


def mark_article_job_failed(conn, job_id: int, error: str, max_attempts: int) -> None:
    """분류 실패 시도 정보를 기록한다."""
    if isinstance(conn, PostgresConnection):
        now = now_iso()
        conn.execute(
            """
            UPDATE article_jobs
            SET status = CASE
                    WHEN COALESCE(attempts, 0) + 1 >= ?
                        THEN 'failed'::public.article_job_status
                    ELSE 'pending'::public.article_job_status
                END,
                attempts = COALESCE(attempts, 0) + 1,
                last_error = ?,
                last_attempt_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (max_attempts, error, now, now, job_id),
        )
        return

    now = now_iso()
    if isinstance(conn, PostgresConnection):
        status = """
            CASE
                WHEN COALESCE(attempts, 0) + 1 >= ? THEN 'failed'::public.article_job_status
                ELSE 'pending'::public.article_job_status
            END
        """
    else:
        status = """
            CASE
                WHEN COALESCE(attempts, 0) + 1 >= ? THEN 'failed'
                ELSE 'pending'
            END
        """
    conn.execute(
        f"""
        UPDATE article_jobs
        SET status = {status},
            attempts = COALESCE(attempts, 0) + 1,
            last_error = ?,
            last_attempt_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (max_attempts, error, now, now, job_id),
    )
