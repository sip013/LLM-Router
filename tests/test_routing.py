import unittest
from unittest.mock import patch

from llm_router.eligibility import eligible_models
from llm_router.jev_client import JevUnavailable
from llm_router.jev_client import choice_is_actionable
from llm_router.pipeline import route_request
from llm_router.policy import rank_models
from llm_router.registry import BY_ID, DEFAULT_LIMITS, Limits, MalformedRequest, narrow_limits


class EligibilityTests(unittest.TestCase):
    def test_image_removes_text_only_models(self):
        kept, rejected = eligible_models(DEFAULT_LIMITS, context_tokens=20, has_image=True)
        self.assertEqual([model.model_id for model in kept], ["qwen/qwen3.8-27b"])
        self.assertTrue(all(item["detail"] == "modality" for item in rejected))

    def test_denylist_is_not_permitted(self):
        limits = Limits(
            max_latency_ms=30_000,
            max_cost_usd=1.0,
            residency_regions=frozenset({"us"}),
            deny_model_ids=frozenset({"openai/gpt-oss-120b"}),
        )
        kept, rejected = eligible_models(limits, context_tokens=20, has_image=False)
        self.assertNotIn("openai/gpt-oss-120b", [model.model_id for model in kept])
        self.assertIn("not_permitted", [item["reason"] for item in rejected])

    def test_caller_cannot_widen_limits(self):
        wider = Limits(max_latency_ms=300_000, max_cost_usd=1.0, residency_regions=frozenset({"us"}))
        with self.assertRaises(MalformedRequest):
            narrow_limits(DEFAULT_LIMITS, wider)


class PolicyTests(unittest.TestCase):
    def test_code_ranks_the_larger_text_model_first(self):
        models = [BY_ID["openai/gpt-oss-20b"], BY_ID["openai/gpt-oss-120b"], BY_ID["qwen/qwen3.8-27b"]]
        ranked = rank_models(models, "code", context_tokens=200)
        self.assertEqual(ranked[0][0].model_id, "openai/gpt-oss-120b")

    def test_chat_ranks_the_smaller_text_model_first(self):
        models = [BY_ID["openai/gpt-oss-20b"], BY_ID["openai/gpt-oss-120b"]]
        ranked = rank_models(models, "chat", context_tokens=50)
        self.assertEqual(ranked[0][0].model_id, "openai/gpt-oss-20b")

    def test_jev_choice_is_placed_first_when_still_eligible(self):
        models = [BY_ID["openai/gpt-oss-20b"], BY_ID["openai/gpt-oss-120b"]]
        ranked = rank_models(models, "chat", context_tokens=50, preferred_id="openai/gpt-oss-120b")
        self.assertEqual(ranked[0][0].model_id, "openai/gpt-oss-120b")


class JevAnswerTests(unittest.TestCase):
    def test_unknown_choice_is_not_actionable(self):
        answer = {"choice": "secret-model", "confidence": 0.99, "probabilities": {"secret-model": 0.99}}
        self.assertFalse(choice_is_actionable(answer, {"openai/gpt-oss-20b"}))

    def test_close_probabilities_are_not_actionable(self):
        answer = {
            "choice": "openai/gpt-oss-20b",
            "confidence": 0.9,
            "probabilities": {"openai/gpt-oss-20b": 0.51, "openai/gpt-oss-120b": 0.49},
        }
        self.assertFalse(choice_is_actionable(answer, set(answer["probabilities"])))


class PipelineTests(unittest.TestCase):
    def test_image_skips_jev_and_selects_the_vision_model(self):
        decision = route_request("describe this", context_tokens=20, has_image=True)
        self.assertEqual(decision["selected"], "qwen/qwen3.8-27b")
        self.assertEqual(decision["analysis_source"], "single_eligible")
        self.assertIsNone(decision["jev"])

    def test_missing_jev_falls_back_to_policy_for_chat(self):
        with patch("llm_router.pipeline.ask", side_effect=JevUnavailable("down")):
            decision = route_request("Hi", context_tokens=5, has_image=False)
        self.assertEqual(decision["analysis_source"], "fallback_heuristic")
        self.assertEqual(decision["selected"], "openai/gpt-oss-20b")


class QualityAndTraceTests(unittest.TestCase):
    def test_labels_become_a_quality_rate_and_canary_rows_are_left_out(self):
        import tempfile
        from pathlib import Path
        from llm_router.quality import build_quality_table
        from llm_router.trace import append_trace, label_last

        with tempfile.TemporaryDirectory() as folder:
            traces = Path(folder) / "traces.jsonl"
            append_trace({
                "status": "completed",
                "task_type": "chat",
                "returned_model_id": "openai/gpt-oss-120b",
                "include_in_quality": False,
                "label": "fail",
            }, traces)
            append_trace({
                "status": "completed",
                "task_type": "chat",
                "returned_model_id": "openai/gpt-oss-20b",
                "include_in_quality": True,
            }, traces)
            self.assertTrue(label_last("pass", traces))
            table = build_quality_table(traces, Path(folder) / "quality.json", min_samples=1)
            self.assertEqual(table["rates"]["openai/gpt-oss-20b"]["chat"]["n"], 1)
            self.assertEqual(table["rates"]["openai/gpt-oss-20b"]["chat"]["rate"], 1.0)
            self.assertNotIn("openai/gpt-oss-120b", table["rates"])

    def test_invalid_json_does_not_call_a_second_model(self):
        from llm_router.execute import run_turn
        calls = []

        def fake_complete(model_id, messages, timeout_s, max_tokens=None):
            calls.append(model_id)
            return {"text": "not json", "input_tokens": 1, "output_tokens": 1}

        with patch("llm_router.execute.complete", fake_complete), patch(
            "llm_router.pipeline.ask", side_effect=JevUnavailable("down")
        ), patch("llm_router.execute.load_policy", return_value={
            "quality_table": None, "candidate_quality_table": None, "shadow": False, "canary_fraction": 0,
        }):
            result = run_turn(
                [{"role": "user", "text": "Hi"}],
                "",
                None,
                lambda history, model_id, image_path: ["hi"],
                response_schema={"type": "object", "required": ["answer"]},
                trace_path=None,
            )
        self.assertEqual(result["validation"], "invalid_json")
        self.assertEqual(calls, ["openai/gpt-oss-20b"])

    def test_rate_limit_skips_the_other_groq_model(self):
        from llm_router.execute import run_turn

        def fake_complete(model_id, messages, timeout_s, max_tokens=None):
            raise RuntimeError("Error code: 429")

        with patch("llm_router.execute.complete", fake_complete), patch("llm_router.pipeline.ask", side_effect=JevUnavailable("down")):
            with self.assertRaises(RuntimeError):
                run_turn(
                    [{"role": "user", "text": "Hi"}],
                    "",
                    None,
                    lambda history, model_id, image_path: ["hi"],
                    trace_path=None,
                )


if __name__ == "__main__":
    unittest.main()
