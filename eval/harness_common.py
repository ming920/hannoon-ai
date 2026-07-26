"""이벤트·토픽 하네스가 공유하는 배관.

두 하네스는 "초기화 → 드레인 → 추출 → 채점 → 진단 → 누적 → 직전 대비 비교"라는 같은 뼈대를
쓴다. 다른 것은 어느 레이어를 초기화하는지와 어떤 지표를 기록하는지뿐이다. 그래서 뼈대만
여기 두고, 레이어별 차이는 각 하네스가 인자로 넘긴다.

복사해 두면 갈라진다 — 특히 CSV 열 정렬, 문자열→숫자 복원, 드레인의 stuck 판정처럼
조용히 틀리는 종류의 코드가 그렇다.
"""
from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


def coerce(value):
    """CSV에서 읽은 문자열을 숫자로 되돌린다.

    CSV는 모든 값을 문자열로 돌려주므로, 이걸 빼먹고 isinstance 로 숫자 검사를 하면
    비교·경고 로직이 조용히 죽는다.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def fmt(value) -> str:
    """리포트·콘솔용 표기. 0~1 사이 실수는 백분율로 본다(충족률·비율)."""
    if value is None or value == "":
        return "N/A"
    if isinstance(value, float):
        return f"{value * 100:.1f}%" if 0 <= value <= 1 else f"{value:.4f}"
    return str(value)


def append_csv(path: Path, columns: list[str], row: dict) -> None:
    """실행 결과를 CSV에 한 줄 덧붙인다 (헤더는 최초 1회)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})


def read_previous_run(path: Path, exclude_run_id: str) -> dict | None:
    """CSV에서 가장 최근 실행(현재 run-id 제외)을 읽는다."""
    if not path.exists():
        return None
    with path.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("run_id") != exclude_run_id]
    return rows[-1] if rows else None


def compare_runs(
    current: dict,
    previous: dict | None,
    *,
    columns: list[str],
    higher_is_better: set[str],
    lower_is_better: set[str],
    skip: tuple[str, ...] = ("run_id", "config_tag", "timestamp"),
) -> list[dict]:
    """직전 실행 대비 변화를 열별로 계산한다.

    higher/lower 어느 쪽에도 없는 수치 열은 "변동"으로만 표시한다 — 방향을 단정할 수 없는
    구조 지표(토픽 개수 등)를 개선/악화로 오판하지 않기 위함이다.
    """
    if not previous:
        return []
    rows = []
    for column in columns:
        if column in skip:
            continue
        cur, prev = current.get(column), coerce(previous.get(column))
        if not isinstance(cur, (int, float)) or not isinstance(prev, (int, float)):
            continue
        delta = cur - prev
        if delta == 0:
            verdict = "유지"
        elif column in higher_is_better:
            verdict = "개선" if delta > 0 else "악화"
        elif column in lower_is_better:
            verdict = "개선" if delta < 0 else "악화"
        else:
            verdict = "변동"
        rows.append({"column": column, "previous": prev, "current": cur,
                     "delta": delta, "verdict": verdict})
    return rows


def patch_dotenv(path: Path, overrides: list[str]) -> str:
    """.env 에 KEY=VALUE 를 적용하고 원본 텍스트를 반환한다 (복원용).

    분류기가 load_dotenv(override=True) 를 쓰므로 프로세스 환경변수 주입은 무시된다.
    .env 파일 자체를 고치는 것이 유일하게 동작하는 방법이다.
    """
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    for kv in overrides:
        key, value = kv.split("=", 1)
        prefix = f"{key}="
        for i, line in enumerate(lines):
            if line.startswith(prefix):
                lines[i] = f"{key}={value}\n"
                break
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(f"{key}={value}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return original


def read_settings(dotenv_path: Path) -> dict:
    """분류기 서브프로세스가 실제로 읽게 될 .env 값을 그대로 읽는다."""
    settings: dict = {}
    if not dotenv_path.exists():
        return settings
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        settings[key.strip()] = value.strip()
    return settings


def drain(
    *,
    cmd: list,
    env: dict,
    remaining_fn,
    label: str,
    log_path: Path,
    hard_cap: int | None = None,
) -> dict:
    """잔량이 0이 될 때까지 분류기를 반복 호출하고 stdout을 파일에 모은다.

    분류기는 한 번에 BATCH_SIZE 건만 처리하고 끝나므로, 백로그를 비우려면 재호출해야 한다.
    stdout에는 진단 로그(JSONL)가 실려 있어 파일로 남긴다.

    한 패스에서 잔량이 줄지 않으면 영구 실패 항목이 있다는 뜻이므로 중단한다 — 그대로 두면
    hard_cap 까지 같은 실패를 반복하며 API 비용만 태운다.

    반환: {"passes", "remaining", "stuck"}
    """
    remaining = remaining_fn()
    cap = hard_cap if hard_cap is not None else max(50, remaining * 2)
    print(f"[{label}] 잔량 {remaining}건, 최대 {cap}패스")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        for pass_num in range(1, cap + 1):
            if remaining == 0:
                print(f"[{label}] 완료 ({pass_num - 1}패스)")
                return {"passes": pass_num - 1, "remaining": 0, "stuck": False}

            result = subprocess.run(
                cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if result.stdout:
                log.write(result.stdout)
                log.flush()
            if result.returncode != 0:
                if result.stderr:
                    print(result.stderr, file=sys.stderr)
                raise RuntimeError(
                    f"{label}: 패스 {pass_num}에서 종료 코드 {result.returncode}로 실패"
                )

            prev, remaining = remaining, remaining_fn()
            print(f"[{label}] 패스 {pass_num}: {prev} → {remaining}건")
            if remaining >= prev:
                print(
                    f"경고: [{label}] 패스 {pass_num}에서 잔량이 줄지 않았습니다 "
                    f"({prev} → {remaining}). 영구 실패 항목이 있을 수 있어 중단합니다.",
                    file=sys.stderr,
                )
                return {"passes": pass_num, "remaining": remaining, "stuck": True}

    return {"passes": cap, "remaining": remaining, "stuck": True}
