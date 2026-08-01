#!/usr/bin/env python3
"""로컬 개발용 임베딩 프록시 — Ollama 임베딩을 프로덕션 스키마 차원에 맞춰 준다.

왜 필요한가
-----------
프로덕션 스키마는 `vector(4096)` 이다(Upstage `solar-embedding` 기준,
`hannoon-supabase/.../20260612120000_change_embeddings_to_4096_dimensions.sql`).
반면 로컬에서 흔히 쓰는 `bge-m3` 는 1024차원이라 그대로는 INSERT 가 실패하고,
`src/embedding.py` 의 `_validate_dimensions` 도 EMBEDDING_DIMENSIONS 와 다르다며 거부한다.

이 프록시는 Ollama 응답 벡터 뒤에 0을 채워 목표 차원으로 맞춘다. 제로 패딩은
**코사인 거리를 바꾸지 않는다** — 내적과 두 노름이 그대로이므로 cos(a,b) 가 보존된다.
pgvector 의 `<=>`(코사인 거리)로 후보를 검색하는 이 파이프라인에서는 안전하다.
실측(bge-m3, 2026-07-31): 유사 문장쌍 0.763091047774 → 패딩 후 동일, 차이 0.0.

  주의: 유클리드 거리(`<->`)나 내적(`<#>`)을 쓰는 쿼리를 나중에 추가한다면 이 전제를
  다시 확인해야 한다. 노름 자체는 보존되지만 차원이 다른 벡터끼리 섞이면 의미가 없다.
  로컬 DB 에 패딩 벡터와 진짜 4096 벡터를 섞어 넣지 말 것 — 둘은 좌표계가 다르다.

사용법
------
    python scripts/local_embedding_proxy.py            # 11500 포트로 기동
    python scripts/local_embedding_proxy.py --port 11500 --target-dim 4096

`.env` 에서 임베딩만 이 프록시로 보낸다(LLM 은 Ollama 로 직접 가면 된다):

    EMBEDDING_BASE_URL=http://127.0.0.1:11500/v1
    EMBEDDING_API_KEY=local
    EMBEDDING_MODEL=bge-m3
    EMBEDDING_PASSAGE_MODEL=bge-m3
    EMBEDDING_QUERY_MODEL=bge-m3
    EMBEDDING_DIMENSIONS=4096
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 11500
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
DEFAULT_TARGET_DIM = 4096
MAX_BODY_BYTES = 32 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    ollama_url = DEFAULT_OLLAMA
    target_dim = DEFAULT_TARGET_DIM

    def log_message(self, fmt, *args):  # noqa: D102 - 기본 접근 로그는 시끄러워서 줄인다
        sys.stderr.write(f"[proxy] {fmt % args}\n")

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 규약
        if not self.path.rstrip("/").endswith("/embeddings"):
            self._send(404, {"error": {"message": f"지원하지 않는 경로입니다: {self.path}"}})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send(400, {"error": {"message": "요청 본문 크기가 올바르지 않습니다."}})
            return

        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            self._send(400, {"error": {"message": f"JSON 파싱 실패: {exc}"}})
            return

        try:
            upstream = self._call_ollama(payload)
        except urllib.error.HTTPError as exc:
            self._send(exc.code, {"error": {"message": f"Ollama 오류: {exc.read().decode(errors='replace')[:300]}"}})
            return
        except (urllib.error.URLError, TimeoutError) as exc:
            self._send(
                502,
                {"error": {"message": f"Ollama 에 연결하지 못했습니다({self.ollama_url}): {exc}"}},
            )
            return

        data = upstream.get("data") or []
        for item in data:
            vec = item.get("embedding")
            if not isinstance(vec, list):
                # 여기 걸리면 패딩 없이 통과해 1024차원이 그대로 나간다. 조용히 넘기지 않는다.
                self._send(
                    502,
                    {
                        "error": {
                            "message": (
                                f"임베딩이 리스트가 아닙니다(type={type(vec).__name__}). "
                                f"encoding_format 강제가 동작하지 않았을 수 있습니다."
                            )
                        }
                    },
                )
                return
            if len(vec) > self.target_dim:
                self._send(
                    400,
                    {
                        "error": {
                            "message": (
                                f"모델이 {len(vec)}차원을 반환했는데 목표 차원은 {self.target_dim} 입니다. "
                                f"잘라내면 의미가 깨지므로 중단합니다 — --target-dim 을 확인하세요."
                            )
                        }
                    },
                )
                return
            if len(vec) < self.target_dim:
                item["embedding"] = vec + [0.0] * (self.target_dim - len(vec))

        self._send(200, upstream)

    def _call_ollama(self, payload: dict) -> dict:
        # OpenAI SDK 는 기본으로 encoding_format="base64" 를 보낸다. 그대로 넘기면 응답의
        # embedding 이 base64 문자열이라 패딩 대상이 되지 못하고 원래 차원이 그대로 나간다.
        # 여기서 float 로 강제해 리스트를 받는다 — SDK 는 응답이 이미 리스트면 디코딩을 건너뛴다.
        payload = dict(payload)
        payload["encoding_format"] = "float"
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.ollama_url.rstrip('/')}/v1/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=int(os.environ.get("EMBED_PROXY_PORT", DEFAULT_PORT)))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA))
    parser.add_argument(
        "--target-dim",
        type=int,
        default=int(os.environ.get("EMBED_TARGET_DIM", DEFAULT_TARGET_DIM)),
        help="프로덕션 스키마의 vector(N) 차원. 기본 4096.",
    )
    args = parser.parse_args()

    Handler.ollama_url = args.ollama_url
    Handler.target_dim = args.target_dim

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(
        f"임베딩 프록시 시작: http://127.0.0.1:{args.port}/v1/embeddings"
        f"  →  {args.ollama_url}  (→ {args.target_dim}차원 제로 패딩)",
        flush=True,
    )
    print("중지하려면 Ctrl+C", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
