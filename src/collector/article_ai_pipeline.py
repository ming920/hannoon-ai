from __future__ import annotations

from .article_llm import ArticleLLMAnalyzer
from .storage import (
    load_pending_ai_pipeline_jobs,
    mark_article_job_failed,
    mark_article_job_sent,
    save_article_analysis_result,
)


def process_pending_articles(
    conn,
    *,
    article_model: str,
    summary_model: str,
    batch_size: int,
    analysis_max_attempts: int = 3,
) -> int:
    """ready 기사에 대해 요약을 LLM으로 수행하고 결과를 저장한다."""
    _validate_args(batch_size=batch_size, analysis_max_attempts=analysis_max_attempts)

    analyzer: ArticleLLMAnalyzer | None = None
    completed = 0
    failed = 0
    batch_no = 0

    while True:
        rows = load_pending_ai_pipeline_jobs(conn, batch_size)
        if not rows:
            break

        batch_no += 1
        batch_completed = 0
        batch_failed = 0
        print(f"[pipeline-batch] {batch_no} -> loaded {len(rows)} pending articles")

        for row in rows:
            job_id = row["job_id"]
            article_id = row["article_id"]
            link = row["link"] or f"article:{article_id}"
            print(f"[pipeline] job={job_id} article={article_id} link={link} -> start")

            existing_summary = row["ai_summary"]
            if existing_summary:
                # 재실행 시 이미 분석된 기사는 비용을 다시 쓰지 않고 큐 상태만 정리한다.
                with conn.transaction():
                    mark_article_job_sent(conn, job_id)
                print(
                    "[pipeline] "
                    f"job={job_id} article={article_id} -> already_analyzed job_status=sent"
                )
                completed += 1
                batch_completed += 1
                continue

            if analyzer is None:
                # 배치에 실제 처리 대상이 있을 때만 클라이언트를 생성해 빈 실행 비용을 피한다.
                print(
                    "[pipeline] initializing LLM analyzer "
                    f"(article={article_model}, summary={summary_model})"
                )
                analyzer = ArticleLLMAnalyzer(
                    article_model=article_model,
                    summary_model=summary_model,
                )

            try:
                result = analyzer.analyze(
                    title=row["title"] or "",
                    subtitle=row["rss_summary"] or "",
                    category=row["category"] or "",
                    content=row["content"] or "",
                )
            except Exception as exc:
                message = str(exc)
                next_attempt = (row["attempts"] or 0) + 1
                next_status = "failed" if next_attempt >= analysis_max_attempts else "pending"
                with conn.transaction():
                    mark_article_job_failed(conn, job_id, message, analysis_max_attempts)
                print(
                    "[warn] pipeline analysis failed: "
                    f"job={job_id} article={article_id} "
                    f"(attempt={next_attempt}/{analysis_max_attempts}, "
                    f"next_status={next_status}, error={message})"
                )
                failed += 1
                batch_failed += 1
                continue

            with conn.transaction():
                save_article_analysis_result(
                    conn,
                    article_id=article_id,
                    summary=result.summary,
                    keywords=result.keywords,
                    status="done",
                )
                mark_article_job_sent(conn, job_id)

            print(
                "[pipeline] "
                f"job={job_id} article={article_id} -> "
                f"summary=saved chars={len(result.summary)} "
                "job_status=sent"
            )
            completed += 1
            batch_completed += 1

        print(
            f"[pipeline-batch] {batch_no} -> "
            f"completed={batch_completed}, failed={batch_failed}, total_completed={completed}"
        )

    if completed == 0 and failed == 0:
        print("[pipeline] no pending ready articles")
    else:
        print(f"[pipeline] completed -> completed={completed}, failed={failed}")

    return completed


def _validate_args(*, batch_size: int, analysis_max_attempts: int) -> None:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0.")
    if analysis_max_attempts <= 0:
        raise ValueError("analysis_max_attempts must be greater than 0.")
