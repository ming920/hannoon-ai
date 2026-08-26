"""이벤트·토픽 하네스가 공유하는 배관.

두 하네스는 "초기화 → 드레인 → 추출 → 채점 → 진단 → 누적 → 직전 대비 비교"라는 같은 뼈대를
쓴다. 다른 것은 어느 레이어를 초기화하는지와 어떤 지표를 기록하는지뿐이다. 그래서 뼈대만
여기 두고, 레이어별 차이는 각 하네스가 인자로 넘긴다.

복사해 두면 갈라진다 — 특히 CSV 열 정렬, 문자열→숫자 복원, 드레인의 stuck 판정처럼
조용히 틀리는 종류의 코드가 그렇다.
"""
from __future__ import annotations

import csv
import hashlib
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


def gold_fingerprint(gold: dict) -> str:
    """채점 기준(정답 쌍 집합)의 짧은 지문.

    정답이 바뀌면 충족률은 분류기가 한 줄도 안 바뀌어도 움직인다. 검수 라운드를 돌 때마다
    정답이 늘어나므로 이 일은 반드시 일어난다 — 실제로 검수 1라운드를 반영하자 같은 스냅샷의
    must-link 가 84.0% → 88.4% 로 "올랐다". 자가 바뀐 것뿐인데 개선으로 읽힌다.

    실행마다 기록해 두고, 지문이 다른 실행끼리는 비교하지 않도록 경고한다.
    """
    parts = []
    for unit in ("event_constraints", "topic_constraints"):
        block = (gold or {}).get(unit) or {}
        for key in ("must_link", "cannot_link"):
            pairs = sorted(tuple(sorted(p)) for p in block.get(key) or [] if len(p) >= 2)
            digest = hashlib.sha1(repr(pairs).encode("utf-8")).hexdigest()
            parts.append(f"{unit}.{key}:{len(pairs)}:{digest}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def gold_change_warning(current: dict, previous: dict | None) -> list[str]:
    """직전 실행과 채점 기준이 다르면 비교하지 말라고 알린다."""
    if not previous:
        return []
    cur = current.get("gold_fingerprint")
    prev = previous.get("gold_fingerprint")
    if not cur or not prev or cur == prev:
        return []
    return [
        f"직전 실행과 **채점 기준(정답)이 다릅니다** ({prev} → {cur}). 충족률 변화는 분류기 "
        "개선이 아니라 자가 바뀐 결과일 수 있습니다 — 같은 정답으로 채점한 실행끼리만 "
        "비교하세요."
    ]


def migrate_csv_header(path: Path, columns: list[str]) -> bool:
    """기존 CSV를 새 열 구성으로 옮겨 적는다. 옮겼으면 True.

    지표 열은 하네스를 개선하면서 늘어난다. 헤더를 최초 1회만 쓰는 구조에서 열이 바뀌면
    새 행이 **옛 헤더 아래에** 다른 순서로 쌓여 값이 엉뚱한 열로 밀려 들어간다. 그러면 추세
    비교가 조용히 거짓말을 하기 시작하므로, 덧붙이기 전에 헤더를 맞춘다.
    """
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        existing = reader.fieldnames or []
        if existing == columns:
            return False
        rows = list(reader)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for old in rows:
            writer.writerow({c: old.get(c, "") for c in columns})

    added = [c for c in columns if c not in existing]
    dropped = [c for c in existing if c not in columns]
    detail = ", ".join(
        part for part in (
            f"추가 {'/'.join(added)}" if added else "",
            f"제거 {'/'.join(dropped)}" if dropped else "",
        ) if part
    ) or "순서 변경"
    print(f"[{path.name}] 열 구성이 바뀌어 기존 {len(rows)}행을 새 헤더로 옮겼습니다 ({detail}). "
          "새로 생긴 열의 과거 값은 비어 있습니다.")
    return True


def append_csv(path: Path, columns: list[str], row: dict) -> None:
    """실행 결과를 CSV에 한 줄 덧붙인다 (헤더는 최초 1회, 열이 바뀌면 기존 행을 옮긴다)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    if not is_new:
        migrate_csv_header(path, columns)
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


# 충족률은 분모가 작으면 몇 쌍만 뒤집혀도 크게 흔들린다. 실제로 이벤트 cannot-link 정답은
# 검수에서 "이 기사는 빼야 한다"고 명시한 6개 이벤트에서만 나와 48쌍이 전부였는데, 리포트에는
# "31.2%"만 찍혀 성능 지표처럼 읽혔다. 토픽 cannot-link 는 아예 0쌍이다.
MIN_RELIABLE_PAIRS = 100


def sample_size_warnings(labeled_sizes) -> list[str]:
    """(이름, 채점된 쌍 수) 목록에서 표본이 얇은 지표에 대한 경고를 만든다.

    0쌍은 "그 실패 양상을 아예 감지할 수 없다"는 뜻이고, 0보다 크지만 얇으면
    "값은 나오지만 추세로 읽으면 안 된다"는 뜻이라 문구를 구분한다.
    """
    warnings = []
    for label, size in labeled_sizes:
        if not size:
            warnings.append(
                f"{label} 정답이 0쌍입니다 — 이 지표로는 해당 실패 양상을 감지할 수 "
                "없습니다. 충족률 N/A 를 '문제 없음'으로 읽지 마세요."
            )
        elif size < MIN_RELIABLE_PAIRS:
            warnings.append(
                f"{label} 정답이 {size:,}쌍뿐입니다 — 몇 쌍만 뒤집혀도 충족률이 크게 "
                "흔들리므로 실행 간 추세 판단에 쓰지 마세요."
            )
    return warnings


def drain(
    *,
    cmd: list,
    env: dict,
    remaining_fn,
    label: str,
    log_path: Path,
    hard_cap: int | None = None,
    log_mode: str = "w",
) -> dict:
    """잔량이 0이 될 때까지 분류기를 반복 호출하고 stdout을 파일에 모은다.

    분류기는 한 번에 BATCH_SIZE 건만 처리하고 끝나므로, 백로그를 비우려면 재호출해야 한다.
    stdout에는 진단 로그(JSONL)가 실려 있어 파일로 남긴다.

    한 패스에서 잔량이 줄지 않으면 영구 실패 항목이 있다는 뜻이므로 중단한다 — 그대로 두면
    hard_cap 까지 같은 실패를 반복하며 API 비용만 태운다.

    분류기는 기사 단위 실패를 stderr 로 흘리고 **종료 코드 0** 으로 끝난다. 종료 코드만 보면
    "정상 종료했는데 잔량이 그대로"라는 진단 불가능한 상태가 되므로, stderr 는 종료 코드와
    무관하게 별도 파일(<로그>-stderr.log)에 남기고 stuck 시 앞부분을 그대로 보여준다.
    stdout 로그는 diagnose_violations 가 JSONL 로 파싱하므로 절대 섞지 않는다.

    log_mode="a" 는 중단된 드레인을 이어서 돌릴 때 쓴다 — 앞선 패스의 진단 로그를 지우면
    이미 분류된 기사의 판단 근거가 사라져 채점 결과를 되짚을 수 없다.

    반환: {"passes", "remaining", "stuck"}
    """
    remaining = remaining_fn()
    cap = hard_cap if hard_cap is not None else max(50, remaining * 2)
    print(f"[{label}] 잔량 {remaining}건, 최대 {cap}패스")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path = log_path.with_name(f"{log_path.stem}-stderr{log_path.suffix}")
    last_stderr = ""
    with log_path.open(log_mode, encoding="utf-8") as log, \
            stderr_path.open(log_mode, encoding="utf-8") as errlog:
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
            if result.stderr:
                last_stderr = result.stderr
                errlog.write(f"── 패스 {pass_num} ──\n{result.stderr}")
                if not result.stderr.endswith("\n"):
                    errlog.write("\n")
                errlog.flush()
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
                _report_stall_stderr(last_stderr, stderr_path)
                return {"passes": pass_num, "remaining": remaining, "stuck": True}

    _report_stall_stderr(last_stderr, stderr_path)
    return {"passes": cap, "remaining": remaining, "stuck": True}


STALL_STDERR_PREVIEW_LINES = 5


def _report_stall_stderr(last_stderr: str, stderr_path: Path) -> None:
    """드레인이 멈췄을 때 마지막 패스의 stderr 앞부분을 보여준다.

    같은 실패가 반복되는 상황이라 앞 몇 줄이면 원인 판별에 충분하다.
    """
    lines = [line for line in last_stderr.splitlines() if line.strip()]
    if not lines:
        print(
            f"  분류기 stderr 없음 — 실패 원인이 기록되지 않았습니다 ({stderr_path})",
            file=sys.stderr,
        )
        return
    print(f"  분류기 stderr (마지막 패스, 총 {len(lines)}줄):", file=sys.stderr)
    for line in lines[:STALL_STDERR_PREVIEW_LINES]:
        print(f"    {line}", file=sys.stderr)
    if len(lines) > STALL_STDERR_PREVIEW_LINES:
        print(f"    … 나머지 {len(lines) - STALL_STDERR_PREVIEW_LINES}줄", file=sys.stderr)
    print(f"  전문: {stderr_path}", file=sys.stderr)
