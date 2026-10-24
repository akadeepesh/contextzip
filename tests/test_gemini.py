"""The Gemini request: the key stays out of the URL, the output budget fits the cap."""

from __future__ import annotations

import unittest
from unittest import mock

from contextzip.ai import gemini


class _Response:
    status_code = 200

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": '["src/a.py"]'}]}}]}


class GeminiRequestTests(unittest.TestCase):
    def call(self, **kwargs):
        with mock.patch.object(gemini.httpx, "post", return_value=_Response()) as post:
            got = gemini.select_files(api_key="secret-key", prompt="fix it", file_tree=[("src/a.py", 10)], ecosystem="python", **kwargs)
        return got, post.call_args

    def test_key_goes_in_a_header(self):
        got, call = self.call()
        self.assertEqual(got, ["src/a.py"])
        self.assertNotIn("secret-key", call.args[0])
        self.assertEqual(call.kwargs["headers"], {"x-goog-api-key": "secret-key"})

    def test_output_budget_scales_with_max_files(self):
        _, small = self.call(max_files=5)
        _, big = self.call(max_files=60)
        budget = lambda c: c.kwargs["json"]["generationConfig"]["maxOutputTokens"]
        self.assertEqual(budget(small), 512)
        self.assertGreaterEqual(budget(big), 60 * 40)
        self.assertLessEqual(gemini._max_output_tokens(10_000), 8192)


if __name__ == "__main__":
    unittest.main()
