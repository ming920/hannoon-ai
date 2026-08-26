"""scripts/local_embedding_proxy.py 유닛테스트.

프록시의 존재 이유는 하나 — 로컬 임베딩(bge-m3, 1024차원)을 프로덕션 스키마의 vector(4096)에
넣을 수 있게 만드는 것이다. 그 전제는 **제로 패딩이 코사인 거리를 바꾸지 않는다**는 것이므로
그 성질을 수치로 고정한다.

실제로 겪은 결함도 함께 고정한다: OpenAI SDK 가 기본으로 보내는 encoding_format="base64" 를
그대로 upstream 에 넘기면 응답 embedding 이 문자열이라 패딩을 조용히 건너뛰고 1024차원이
그대로 나갔다.

실행: python -m unittest tests.test_local_embedding_proxy
"""
from __future__ import annotations

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from local_embedding_proxy import (  # noqa: E402
    EmbeddingShapeError,
    force_float_encoding,
    pad_embeddings,
)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb)


def _payload(*vectors) -> dict:
    return {"object": "list", "data": [{"embedding": list(v), "index": i} for i, v in enumerate(vectors)]}


class PaddingTests(unittest.TestCase):
    def test_pads_to_target_dimension(self):
        out = pad_embeddings(_payload([0.1] * 1024), 4096)
        vec = out["data"][0]["embedding"]
        self.assertEqual(len(vec), 4096)
        self.assertEqual(set(vec[1024:]), {0.0}, "패딩 구간은 전부 0 이어야 한다")

    def test_pads_every_item_in_a_batch(self):
        out = pad_embeddings(_payload([0.1] * 1024, [0.2] * 1024, [0.3] * 1024), 4096)
        self.assertEqual([len(d["embedding"]) for d in out["data"]], [4096, 4096, 4096])

    def test_leaves_matching_dimension_untouched(self):
        original = [0.5] * 4096
        out = pad_embeddings(_payload(original), 4096)
        self.assertEqual(out["data"][0]["embedding"], original)

    def test_empty_data_is_not_an_error(self):
        self.assertEqual(pad_embeddings({"data": []}, 4096)["data"], [])


class CosinePreservationTests(unittest.TestCase):
    """패딩이 후보 검색 결과를 바꾸지 않는다는 근거."""

    def test_cosine_is_identical_after_padding(self):
        a = [0.31, -0.72, 0.14, 0.55, -0.09]
        b = [0.28, -0.61, 0.44, 0.10, -0.33]
        before = _cosine(a, b)
        out = pad_embeddings(_payload(a, b), 4096)
        after = _cosine(out["data"][0]["embedding"], out["data"][1]["embedding"])
        self.assertAlmostEqual(before, after, places=12)

    def test_relative_ordering_survives(self):
        # 검색은 "누가 더 가까운가"만 쓰므로 순서가 뒤집히지 않는 것이 핵심이다.
        query = [1.0, 0.0, 0.0, 0.0]
        near = [0.9, 0.1, 0.0, 0.0]
        far = [0.0, 0.0, 1.0, 0.0]
        out = pad_embeddings(_payload(query, near, far), 512)
        q, n, f = (d["embedding"] for d in out["data"])
        self.assertGreater(_cosine(q, n), _cosine(q, f))


class ShapeErrorTests(unittest.TestCase):
    def test_base64_string_is_rejected_not_silently_passed(self):
        # 조용히 통과하면 패딩 없이 원래 차원이 나가 INSERT 가 깨진다.
        payload = {"data": [{"embedding": "eJxjYGBgYGRgZGBkYGJgZmBhYGVgY2Bn4GDgZOACAA=="}]}
        with self.assertRaises(EmbeddingShapeError) as ctx:
            pad_embeddings(payload, 4096)
        self.assertEqual(ctx.exception.status, 502)

    def test_oversized_vector_is_refused_rather_than_truncated(self):
        with self.assertRaises(EmbeddingShapeError) as ctx:
            pad_embeddings(_payload([0.1] * 8192), 4096)
        self.assertEqual(ctx.exception.status, 400)

    def test_missing_embedding_key_is_rejected(self):
        with self.assertRaises(EmbeddingShapeError):
            pad_embeddings({"data": [{"index": 0}]}, 4096)


class EncodingFormatTests(unittest.TestCase):
    def test_forces_float(self):
        self.assertEqual(force_float_encoding({"model": "bge-m3"})["encoding_format"], "float")

    def test_overrides_base64_from_the_sdk(self):
        forced = force_float_encoding({"model": "bge-m3", "encoding_format": "base64"})
        self.assertEqual(forced["encoding_format"], "float")

    def test_does_not_mutate_caller_payload(self):
        original = {"model": "bge-m3", "encoding_format": "base64"}
        force_float_encoding(original)
        self.assertEqual(original["encoding_format"], "base64")

    def test_other_fields_are_preserved(self):
        forced = force_float_encoding({"model": "bge-m3", "input": ["a", "b"]})
        self.assertEqual(forced["model"], "bge-m3")
        self.assertEqual(forced["input"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
