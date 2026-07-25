# 검수 정답 데이터 (constraints-v1)

사람이 직접 검수해 만든 **기사 쌍 제약** 정답이다. `../dummy_articles.json` / `../gold_labels.json`이
`generate_dummy.py`가 만드는 **합성 픽스처**인 것과 달리, 이 디렉터리의 파일은 **재생성이 불가능한
수작업 산출물**이다. 실수로 덮어쓰지 말 것.

| 파일 | 설명 |
|------|------|
| `review_2026-07-20.json` | 2026-07-07 스냅샷 대상 검수 결과. 검수자 정예은·오재민, 2026-07-20 내보냄 |

## 형식

```
schema_version     "constraints-v1"
snapshot_date      검수 대상 스냅샷 시각
reviewer           검수자
exported_at        검수 UI에서 내보낸 시각
article_pool       검수 대상 기사 id 목록 (2,561건)
event_constraints  { must_link: [[id, id], ...], cannot_link: [[id, id], ...] }
topic_constraints  { must_link: [[id, id], ...], cannot_link: [[id, id], ...] }
review_log         [{ unit, ref_id, label, excluded_articles, memo }]  (100건)
_work              검수 UI 내부 작업 상태 (판정에 쓰지 않음)
```

`review_2026-07-20.json`의 실측 규모:

| 제약 | 쌍 수 |
|------|------|
| 이벤트 must_link | 1,244 |
| 이벤트 cannot_link | 48 |
| 토픽 must_link | 1,309 |
| 토픽 cannot_link | 0 |

`review_log`는 전부 `unit="event"`이고 label 분포는 `ok` 49 / `split` 34 / `vague` 11 / `merge` 6,
그중 메모가 달린 것은 16건이다.

> **주의**: `review_log`의 메모는 *검수 당시 이벤트 id*(`ref_id`) 기준이라, 위반한 **기사 쌍**에
> 자동으로 연결할 수 없다(`constraints-v1`에 기사→원본이벤트 맵이 없다). `constraint_checks.py`는
> 위반 쌍이 갈린 이벤트/토픽 id를 보여주고, 메모는 별도 섹션에 참고용으로 출력한다.

## 사용법

```powershell
# 1) 측정만 (종료 코드 항상 0)
python eval/constraint_checks.py eval/data/constraints/review_2026-07-20.json snapshot.json

# 2) 현재 결과를 기준선으로 저장
python eval/constraint_checks.py `
    eval/data/constraints/review_2026-07-20.json snapshot.json `
    --write-baseline eval/data/constraints/baseline.json

# 3) 회귀 게이트 (기준선 대비 하락 시 종료 코드 1)
python eval/constraint_checks.py `
    eval/data/constraints/review_2026-07-20.json snapshot.json `
    --baseline eval/data/constraints/baseline.json
```

### 통과 기준은 "위반 0"이 아니다

구 도구(`han-noon_test.exe`)의 README는 "위반이 하나라도 있으면 종료 코드 1"이라고 기술했으나
**폐기됐다**. must_link만 이벤트 1,244쌍 + 토픽 1,309쌍이라 LLM 군집화가 전부 맞출 수는 없고,
그 기준으로는 게이트가 첫날부터 영구 실패해 무용지물이 된다.

현재 기준은 **기준선 대비 충족률 하락 없음**이다. 실행 간 변동이 관측되면 `--tolerance`로 허용
하락폭을 올린다(예: `--tolerance 0.02` → 2%p까지 허용).

## 결과 스냅샷(비교 대상) 추출 SQL

Supabase 대시보드 → SQL Editor에서 실행한다. 대상 테이블은
`events / articles / article_ai_results / event_articles`.

### 이벤트 + 기사

```sql
select json_build_object(
  'snapshot_date', to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS'),
  'events', (
    select coalesce(json_agg(e), '[]'::json)
    from (
      select
        ev.id,
        ev.title,
        ev.summary,
        ev.core_content,
        ev.created_at,
        ev.article_count,
        (
          select coalesce(json_agg(a order by a.published_at), '[]'::json)
          from (
            select
              ar.id,
              ar.title,
              ar.publisher,
              ar.published_at,
              air.summary,        -- AI 생성 요약 (검수 보조용)
              ar.link
            from event_articles ea
            join articles ar on ar.id = ea.article_id
            left join article_ai_results air on air.article_id = ar.id
            where ea.event_id = ev.id
          ) a
        ) as articles
      from events ev
      where ev.id > 0           -- 직전 배치의 마지막 이벤트 id (누적 시 이 값만 갱신)
      order by ev.id asc         -- id 오름차순: 데이터가 추가돼도 앞쪽이 고정
      limit 100                  -- 한 번에 검수할 양
    ) e
  )
) as result;
```

### 토픽 + 이벤트 (구성 기사 id 포함)

```sql
select json_build_object(
  'topics', (
    select coalesce(json_agg(t), '[]'::json)
    from (
      select tp.id, tp.title, tp.created_at,
        (
          select coalesce(json_agg(ev order by ev.created_at), '[]'::json)
          from (
            select
              e.id, e.title, e.created_at, e.prev_event_id, e.next_event_id, e.core_content,
              (select coalesce(json_agg(ea.article_id), '[]'::json)
               from event_articles ea where ea.event_id = e.id) as article_ids
            from events e where e.topic_id = tp.id
          ) ev
        ) as events
      from topics tp
      where tp.id > 0           -- 직전 배치의 마지막 토픽 id
      order by tp.id asc
      limit 50
    ) t
  )
) as result;
```

두 결과를 `{snapshot_date, events, topics}` 한 JSON으로 합쳐 도구에 넣는다. `events`만 먼저 뽑아
이벤트 제약부터 확인해도 동작한다(토픽 검사는 `불가`로 집계된다).

> **정렬·필터는 id 오름차순**으로 한다. id는 단조 증가하는 정수라 원천 기사가 계속 추가돼도 이미
> 검수한 앞쪽 구간이 밀리지 않는다. `created_at`과 달리 타임존·동시각·null 걱정이 없고, 분류
> 파이프라인의 순차 처리 순서와도 일치한다. 구간을 끊어 누적 검수하려면
> `where ev.id > 직전_배치_마지막_id`의 커서 값만 갱신하면 된다(토픽도 동일).

### 입력 JSON 형식 (도구가 읽는 것)

```json
{
  "snapshot_date": "2026-06-28T14:30:00",
  "events": [
    {
      "id": 101,
      "title": "전공의 집단 사직",
      "summary": "전공의들이 정부 의대 증원에 반발해 집단 사직서를 제출",
      "core_content": "전공의 집단 사직서 제출",
      "created_at": "2026-03-01T09:00:00",
      "article_count": 12,
      "articles": [
        {
          "id": 9001,
          "title": "전공의 1만명 사직서 제출",
          "publisher": "A신문",
          "published_at": "2026-03-01T08:10:00",
          "summary": "전국 수련병원 전공의 약 1만명이 사직서를 제출했다.",
          "link": "https://example.com/9001"
        }
      ]
    }
  ],
  "topics": [
    {
      "id": 5, "title": "의료개혁", "created_at": "2026-02-20T00:00:00",
      "events": [
        {
          "id": 100, "title": "의대 증원 발표", "created_at": "2026-02-20T10:00:00",
          "prev_event_id": null, "next_event_id": 101,
          "core_content": "정부가 의대 정원 2000명 증원안 발표",
          "article_ids": [8801, 8802]
        }
      ]
    }
  ]
}
```

판정에 실제로 쓰이는 필드는 `events[].id`, `events[].articles[].id`, `topics[].id`,
`topics[].events[].article_ids` 넷뿐이다. 나머지는 사람이 읽기 위한 것이라 없어도 동작한다.

## 출력 예

```
  제약                                충족  위반  불가    충족률
  --------------------------------------------------------------
  이벤트 must-link  (같이 있어야)      938   306     0     75.4%
  이벤트 cannot-link(떨어져야)          48     0     0    100.0%
  토픽   must-link  (같은 토픽)       1309     0     0    100.0%
  토픽   cannot-link(다른 토픽)          0     0     0       N/A
```

- **충족률** = 충족 / (충족 + 위반). **"불가"**(결과 스냅샷에 그 기사가 없음)는 분모에서 제외한다.
- 제약이 0쌍인 항목은 `N/A`로 나오고 기준선 비교에서도 `비교불가`로 건너뛴다.

## 시딩과의 관계

정답이 참조하는 기사가 결과 스냅샷에 없으면 전부 "불가"로 집계돼 충족률이 `N/A`가 된다.
비교 대상 DB에 원천 기사를 넣는 절차는 `../seed/README.md`를 참고한다.
