-- 서브토픽(계층) 분류 기능용 Supabase/Postgres 마이그레이션
-- 적용은 운영 마이그레이션 흐름에서 수동으로 수행한다(앱은 운영 DDL을 실행하지 않는다).
--
-- 효과:
--   - topics 테이블에 자기참조 parent_topic_id 컬럼을 추가한다.
--   - parent_topic_id IS NULL  → 최상위 토픽
--   - parent_topic_id = <id>   → 해당 부모 아래 서브토픽(events.topic_id가 가리키는 leaf)
--   - 부모 스코프 검색(roots_only / parent_topic_id 필터)을 위한 인덱스를 추가한다.
--
-- 주의:
--   - topics.id 타입에 맞춰 컬럼 타입을 정한다. 아래는 bigint identity 가정이다.
--     id가 integer면 BIGINT 대신 INTEGER로 맞춘다.
--   - 기존 토픽은 모두 parent_topic_id NULL(최상위)로 남으므로 평면 동작과 호환된다.

ALTER TABLE public.topics
    ADD COLUMN IF NOT EXISTS parent_topic_id BIGINT;

-- 자기참조 외래키. 부모가 삭제되면 서브토픽은 최상위로 승격(SET NULL)되도록 둔다.
-- 부모 삭제 시 서브토픽까지 함께 지우려면 ON DELETE CASCADE로 바꾼다.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'topics_parent_topic_id_fkey'
    ) THEN
        ALTER TABLE public.topics
            ADD CONSTRAINT topics_parent_topic_id_fkey
            FOREIGN KEY (parent_topic_id)
            REFERENCES public.topics(id)
            ON DELETE SET NULL;
    END IF;
END $$;

-- 부모 스코프 후보 검색(WHERE t.parent_topic_id = ? / IS NULL) 성능용 인덱스.
CREATE INDEX IF NOT EXISTS idx_topics_parent_topic_id
    ON public.topics(parent_topic_id);
