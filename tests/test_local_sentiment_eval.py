import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from unittest.mock import MagicMock, Mock, patch

from scripts.evaluate_local_sentiment import (
    ANALYSIS_TEXT_VERSION, VARIANTS, digest, evaluate_one, load_samples, local_base_url, parser, run, summarize,
)


def decision(**changes):
    values = dict(content_type="opinion", sentiment_label="neutral", sentiment_score=0,
                  emotion_tags=[], emotion_intensity=0, intent="neutral", intent_strength=0,
                  position_status="unknown", market_outlook="unknown", confidence=0.8, reasoning="没有明确情绪")
    values.update(changes)
    return values


def response(payload, status=200):
    item = Mock(status_code=status)
    item.json.return_value = payload
    return item


def completion(payload, finish="stop"):
    return response({"choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": finish}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 20}})


def sample(gold=None):
    return dict(id="a", title="今天清仓了", content="", sector="gold", platform="xueqiu", gold=gold or {})


class LocalSentimentEvalTests(unittest.TestCase):
    def args(self, *extra):
        return parser().parse_args(["--smoke", "--variant", "prompt", *extra])

    def test_rejects_external_endpoints_and_redirect_locations(self):
        for url in ("https://example.com/v1", "https://8.8.8.8", "http://127.0.0.1@evil.test",
                    "http://127.0.0.1/?url=evil", "http://127.0.0.1:99999"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                local_base_url(url)
        self.assertEqual(local_base_url("http://100.83.225.31:8080/"), "http://100.83.225.31:8080/v1")

    def test_preflight_failure_never_calls_completion_or_creates_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "run"
            session = Mock()
            session.get.return_value = response({}, 503)
            with self.assertRaises(ValueError):
                run(self.args("--output-dir", str(target)), session)
            session.post.assert_not_called()
            self.assertFalse(target.exists())
            self.assertFalse(session.trust_env)
            self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

    def test_model_mismatch_stops_before_generation(self):
        session = Mock()
        session.get.return_value = response({"data": [{"id": "wrong-model"}]})
        with self.assertRaises(ValueError):
            run(self.args("--model", "expected-model"), session)
        session.post.assert_not_called()

    def test_empty_json_retries_same_input_and_retains_all_failures(self):
        session = Mock()
        session.post.return_value = completion({})
        row = evaluate_one(session, "http://127.0.0.1:8080/v1", "local", sample(), "prompt", 42, 1, self.args())
        self.assertEqual(row["status"], "failed")
        self.assertEqual(len(row["attempts"]), 3)
        self.assertTrue(all(a["raw_output"] == "{}" and "missing required" in a["error"] for a in row["attempts"]))
        bodies = [call.kwargs["json"] for call in session.post.call_args_list]
        self.assertTrue(all(body == bodies[0] for body in bodies))

    def test_truncated_complete_looking_json_is_still_failure(self):
        session = Mock()
        session.post.return_value = completion(decision(), "length")
        row = evaluate_one(session, "http://127.0.0.1:8080/v1", "local", sample(), "prompt", 42, 1, self.args())
        self.assertEqual(row["status"], "failed")
        self.assertEqual(len(row["attempts"]), 3)

    def test_malformed_response_envelopes_are_retried_and_retained(self):
        malformed = [None, [], {"choices": {}}, {"choices": [None]},
                     {"choices": [{"message": []}]}, {"choices": [{"message": {"content": 7}}]}]
        for envelope in malformed:
            with self.subTest(envelope=envelope):
                session = Mock()
                session.post.return_value = response(envelope)
                row = evaluate_one(session, "http://127.0.0.1:8080/v1", "local", sample(), "prompt", 42, 1, self.args())
                self.assertEqual(row["status"], "failed")
                self.assertEqual(len(row["attempts"]), 3)
                self.assertTrue(all(attempt["error"] for attempt in row["attempts"]))

    def test_invalid_preflight_envelope_stops_without_completion(self):
        session = Mock()
        session.get.return_value = response([])
        with self.assertRaises(ValueError):
            run(self.args(), session)
        session.post.assert_not_called()

    def test_seed_range_and_duplicates(self):
        for value in ("-2", "4294967296"):
            with self.subTest(seed=value), patch("sys.stderr"), self.assertRaises(SystemExit):
                self.args("--seeds", value)
        with tempfile.TemporaryDirectory() as tmp:
            session = Mock()
            session.get.return_value = response({"data": [{"id": "local"}]})
            session.post.return_value = completion(decision())
            with patch("builtins.print"):
                report, directory = run(self.args("--limit", "1", "--seeds", "42", "42", "--output-dir", str(Path(tmp) / "run")), session)
            self.assertEqual(report["expected_runs"], 1)
            self.assertEqual(json.loads((directory / "metadata.json").read_text())["seeds"], [42])

    def test_no_gold_run_is_unscored_and_writes_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Mock()
            session.get.return_value = response({"data": [{"id": "local"}]})
            session.post.return_value = completion(decision())
            with patch("builtins.print"):
                report, folder = run(self.args("--limit", "1", "--output-dir", str(Path(tmp) / "run")), session)
            metrics = report["variants"]["prompt"]
            self.assertEqual(metrics["scoring_status"], "unscored_no_human_gold")
            self.assertEqual(metrics["fields"], {})
            self.assertIsNone(metrics["emotion"])
            self.assertIsNone(metrics["score"]["mae_points_valid_only"])
            checkpoint = json.loads((folder / "results.jsonl").read_text())
            self.assertEqual(checkpoint["analysis_text"], "今天清仓了")
            self.assertEqual(checkpoint["status"], "ok")
            self.assertEqual(json.loads((folder / "metadata.json").read_text())["status"], "complete")

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "keep.txt"
            marker.write_text("preserve")
            session = Mock()
            with self.assertRaises(FileExistsError):
                run(self.args("--output-dir", tmp), session)
            session.get.assert_not_called()
            self.assertEqual(marker.read_text(), "preserve")

    def test_failure_stays_in_accuracy_denominator(self):
        rows = [
            dict(id="a", variant="prompt", seed=42, status="ok", seconds=1,
                 gold={"sentiment_label": "neutral", "sentiment_score": 0}, decision=decision()),
            dict(id="b", variant="prompt", seed=42, status="failed", seconds=2,
                 gold={"sentiment_label": "fear", "sentiment_score": -0.6}, decision=None),
        ]
        score = summarize(rows)["prompt"]
        self.assertEqual(score["fields"]["sentiment_label"]["accuracy"], 0.5)
        self.assertEqual(score["score"]["valid"], 1)
        self.assertEqual(score["score"]["failed"], 1)
        self.assertEqual(score["score"]["mae_points_valid_only"], 0)
        self.assertEqual(score["score"]["within_20_points_including_failures"], 0.5)
        self.assertEqual(score["emotion"]["macro_f1"], 0.5)

    def test_across_seed_consistency_requires_distinct_seeds(self):
        first = dict(id="a", variant="prompt", seed=42, status="ok", seconds=1,
                     gold={}, decision=decision())
        rows = [dict(first, repeat=1), dict(first, repeat=2)]
        metrics = summarize(rows)["prompt"]
        self.assertEqual(metrics["repeat_same_seed"], {"groups": 1, "all_equal_rate": 1})
        self.assertEqual(metrics["repeat_across_seeds"], {"groups": 0, "all_equal_rate": None})
        rows.append(dict(first, seed=43, repeat=1))
        self.assertEqual(summarize(rows)["prompt"]["repeat_across_seeds"],
                         {"groups": 1, "all_equal_rate": 1})

    def test_partial_gold_is_not_filled_and_bad_labels_fail_before_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.jsonl"
            path.write_text(json.dumps(dict(sample({"intent": "clear"}), split="dev")) + "\n")
            rows, _ = load_samples(path, split="dev")
            self.assertEqual(rows[0]["gold"], {"intent": "clear"})
            path.write_text(json.dumps(sample({"sentiment_score": 60})) + "\n")
            with self.assertRaises(ValueError):
                load_samples(path)
            path.write_text(json.dumps(sample({"intent": "not-an-enum"})) + "\n")
            with self.assertRaises(ValueError):
                load_samples(path)

    def test_duplicate_input_id_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.jsonl"
            path.write_text(json.dumps(sample()) + "\n" + json.dumps(sample()) + "\n")
            with self.assertRaises(ValueError):
                load_samples(path)

    def fake_runtime(self):
        runtime = MagicMock(enabled=False)
        def start():
            os.environ.update(QWEN_BASE_URL="http://127.0.0.1:8099", MOM_INDEX_LLM_MODEL="started-local",
                              MOM_INDEX_LLM_API_KEY="local-no-auth")
            return runtime
        runtime.__enter__.side_effect = start
        runtime.__exit__.return_value = False
        return runtime

    @staticmethod
    def local_config(section, key, default):
        return {"port": 8099, "request_timeout_seconds": 222}.get(key, default)

    def test_standalone_owns_runtime_overrides_environment_and_uses_config_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self.fake_runtime()
            session = Mock()
            session.get.return_value = response({"data": [{"id": "started-local"}]})
            session.post.return_value = completion(decision())
            with patch.dict(os.environ, {"QWEN_BASE_URL": "http://100.83.225.31:8080", "MOM_INDEX_LLM_API_KEY": "original"}), \
                    patch("scripts.evaluate_local_sentiment.LocalLlmRuntime", return_value=runtime), \
                    patch("scripts.evaluate_local_sentiment.ini_get_int", side_effect=self.local_config), \
                    patch("builtins.print"):
                report, directory = run(self.args("--start-local-model", "--limit", "1", "--output-dir", str(Path(tmp) / "run")), session)
                self.assertEqual(os.environ["QWEN_BASE_URL"], "http://100.83.225.31:8080")
                self.assertEqual(os.environ["MOM_INDEX_LLM_API_KEY"], "original")
            self.assertTrue(runtime.enabled)
            runtime.__enter__.assert_called_once()
            runtime.__exit__.assert_called_once_with(None, None, None)
            self.assertEqual(session.get.call_args.args[0], "http://127.0.0.1:8099/v1/models")
            self.assertEqual(session.post.call_args.kwargs["json"]["model"], "started-local")
            self.assertEqual(session.post.call_args.kwargs["timeout"], 222)
            self.assertEqual(report["variants"]["prompt"]["successes"], 1)
            self.assertTrue(json.loads((directory / "metadata.json").read_text())["started_local_model"])

    def test_standalone_stops_after_partial_failure_and_honors_explicit_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self.fake_runtime()
            session = Mock()
            session.get.return_value = response({"data": [{"id": "started-local"}]})
            session.post.side_effect = [completion(decision()), completion({}), completion({}), completion({})]
            with patch("scripts.evaluate_local_sentiment.LocalLlmRuntime", return_value=runtime), \
                    patch("scripts.evaluate_local_sentiment.ini_get_int", side_effect=self.local_config), patch("builtins.print"):
                report, directory = run(self.args("--start-local-model", "--limit", "2", "--timeout", "33", "--output-dir", str(Path(tmp) / "run")), session)
            self.assertEqual(report["variants"]["prompt"]["failures"], 1)
            self.assertEqual(len((directory / "results.jsonl").read_text().splitlines()), 2)
            self.assertEqual(session.post.call_args.kwargs["timeout"], 33)
            runtime.__exit__.assert_called_once_with(None, None, None)

    def test_standalone_stops_on_preflight_failure_and_keyboard_interrupt(self):
        for interrupt in (False, True):
            with self.subTest(interrupt=interrupt), tempfile.TemporaryDirectory() as tmp:
                runtime = self.fake_runtime()
                session = Mock()
                session.get.return_value = response({"data": [{"id": "started-local"}]}) if interrupt else response({}, 503)
                session.post.side_effect = [completion(decision()), KeyboardInterrupt()]
                target = Path(tmp) / "run"
                with patch("scripts.evaluate_local_sentiment.LocalLlmRuntime", return_value=runtime), \
                        patch("scripts.evaluate_local_sentiment.ini_get_int", side_effect=self.local_config), patch("builtins.print"):
                    with self.assertRaises(KeyboardInterrupt if interrupt else ValueError):
                        run(self.args("--start-local-model", "--limit", "2", "--output-dir", str(target)), session)
                runtime.__exit__.assert_called_once()
                self.assertIs(runtime.__exit__.call_args.args[0], KeyboardInterrupt if interrupt else ValueError)
                if interrupt:
                    self.assertEqual(len((target / "results.jsonl").read_text().splitlines()), 1)
                else:
                    session.post.assert_not_called()
                    self.assertFalse(target.exists())

    def test_standalone_checks_input_output_and_explicit_address_before_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_input = Path(tmp) / "bad.jsonl"
            bad_input.write_text("{}\n")
            invalid = [
                parser().parse_args(["--input", str(bad_input), "--start-local-model"]),
                self.args("--start-local-model", "--output-dir", tmp),
                self.args("--start-local-model", "--output-dir", str(bad_input / "run")),
                self.args("--start-local-model", "--base-url", "http://100.83.225.31:8080"),
            ]
            for args in invalid:
                with self.subTest(args=args), patch("scripts.evaluate_local_sentiment.LocalLlmRuntime") as factory:
                    with self.assertRaises((ValueError, OSError)):
                        run(args, Mock())
                    factory.assert_not_called()

    def test_standalone_rejects_model_conflict_and_stops(self):
        runtime = self.fake_runtime()
        with patch("scripts.evaluate_local_sentiment.LocalLlmRuntime", return_value=runtime), \
                patch("scripts.evaluate_local_sentiment.ini_get_int", side_effect=self.local_config):
            session = Mock()
            with self.assertRaisesRegex(ValueError, "--model"):
                run(self.args("--start-local-model", "--model", "different-model"), session)
        runtime.__exit__.assert_called_once()
        session.get.assert_not_called()

    def test_standalone_port_conflict_never_starts_or_kills_a_process(self):
        with patch("local_llm_runtime.Path.is_file", return_value=True), \
                patch("local_llm_runtime.shutil.which", return_value="/mock/llama-server"), \
                patch("local_llm_runtime.LocalLlmRuntime._require_free_port", side_effect=RuntimeError("port occupied")), \
                patch("local_llm_runtime.LocalLlmRuntime._wait_ready", side_effect=RuntimeError("port occupied")), \
                patch("local_llm_runtime.subprocess.Popen") as popen, patch("local_llm_runtime.os.killpg") as killpg:
            with self.assertRaisesRegex(RuntimeError, "port occupied"):
                run(self.args("--start-local-model"), Mock())
        popen.assert_not_called()
        killpg.assert_not_called()

    def test_mysql_freezes_before_model_start_and_reuses_identical_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "random-run"
            samples = [dict(sample(), id=str(i), title=f"真实帖子{i}", split="random") for i in range(3)]
            session = Mock()
            session.get.return_value = response({"data": [{"id": "local"}]})
            session.post.return_value = completion(decision())

            @contextmanager
            def runtime(args):
                self.assertEqual(len((target / "samples.jsonl").read_text().splitlines()), 3)
                self.assertEqual(json.loads((target / "sampling.json").read_text())["status"], "frozen")
                yield "http://127.0.0.1:8080/v1", "local", 120

            args = parser().parse_args(["--mysql", "--sample-size", "3", "--days", "30",
                                       "--sample-seed", "11", "--start-local-model", "--output-dir", str(target)])
            with patch("scripts.sentiment_mysql_sample.sample_mysql_posts", return_value=(samples, {"pool_count": 1000})) as sampler, \
                    patch("scripts.evaluate_local_sentiment.evaluation_runtime", side_effect=runtime), patch("builtins.print"):
                report, folder = run(args, session)
            sampler.assert_called_once_with(days=30, count=3, seed=11, max_chars=1200)
            self.assertEqual(report["completed_runs"], 6)
            sent = [json.loads(c.kwargs["json"]["messages"][1]["content"])["analysis_text"]
                    for c in session.post.call_args_list]
            self.assertEqual(sent[:3], sent[3:])
            self.assertTrue(all(v["scoring_status"] == "unscored_no_human_gold" for v in report["variants"].values()))
            with zipfile.ZipFile(folder.parent / (folder.name + ".zip")) as archive:
                self.assertIn(folder.name + "/samples.jsonl", archive.namelist())
                self.assertIn(folder.name + "/sampling.json", archive.namelist())
                self.assertIn(folder.name + "/comparison.html", archive.namelist())
                self.assertIn(folder.name + "/summary.json", archive.namelist())

    def test_mysql_preflight_failure_retains_frozen_sample_without_generating(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "failed-preflight"
            session = Mock()
            session.get.return_value = response({}, 503)
            args = parser().parse_args(["--mysql", "--sample-size", "1", "--output-dir", str(target)])
            with patch("scripts.sentiment_mysql_sample.sample_mysql_posts", return_value=([sample()], {})), patch("builtins.print"):
                with self.assertRaises(ValueError):
                    run(args, session)
            self.assertTrue((target / "samples.jsonl").exists())
            session.post.assert_not_called()
            self.assertFalse((target / "metadata.json").exists())
            self.assertFalse((target.parent / (target.name + ".zip")).exists())

    def test_mysql_short_pool_stops_before_starting_model(self):
        with patch("scripts.sentiment_mysql_sample.sample_mysql_posts", side_effect=ValueError("不足100条")), \
                patch("scripts.evaluate_local_sentiment.LocalLlmRuntime") as runtime:
            session = Mock()
            with self.assertRaisesRegex(ValueError, "不足100条"):
                run(parser().parse_args(["--mysql", "--start-local-model"]), session)
            runtime.assert_not_called()
            session.get.assert_not_called()

    def test_mysql_rejects_limit_and_existing_archive_before_database_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "run"
            archive = Path(tmp) / "run.zip"
            archive.write_bytes(b"keep")
            for extra in (["--limit", "10"], ["--output-dir", str(target)]):
                with self.subTest(extra=extra), patch("scripts.sentiment_mysql_sample.sample_mysql_posts") as sampler:
                    with self.assertRaises((ValueError, FileExistsError)):
                        run(parser().parse_args(["--mysql", *extra]), Mock())
                    sampler.assert_not_called()
            self.assertEqual(archive.read_bytes(), b"keep")

    def test_dataset_hash_mismatch_stops_before_runtime_and_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "samples.jsonl"
            source.write_text(json.dumps(sample()) + "\n")
            target = Path(tmp) / "run"
            args = parser().parse_args(["--input", str(source), "--expect-dataset-sha256", "0" * 64,
                                       "--start-local-model", "--output-dir", str(target)])
            session = Mock()
            with patch("scripts.evaluate_local_sentiment.LocalLlmRuntime") as runtime:
                with self.assertRaisesRegex(ValueError, "样本摘要不匹配"):
                    run(args, session)
                runtime.assert_not_called()
            session.get.assert_not_called()
            session.post.assert_not_called()
            self.assertFalse(target.exists())

    def test_dataset_hash_argument_rejects_non_sha256(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            self.args("--expect-dataset-sha256", "not-a-hash")

    def test_refined_pair_preserves_frozen_samples_and_shares_corrected_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "samples.jsonl"
            samples = [dict(sample(), analysis_text="旧标题片段"),
                       dict(sample(), id="b", title="原始完整标题", analysis_text="修正后：原始完整标题")]
            original_bytes = "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples).encode()
            source.write_bytes(original_bytes)
            target = Path(tmp) / "run"
            expected_hash = digest(samples)
            args = parser().parse_args(["--input", str(source), "--expect-dataset-sha256", expected_hash,
                                       "--base-url", "http://100.83.225.31:8080", "--variant", "prompt",
                                       "--variant", "refined", "--output-dir", str(target)])
            session = Mock()
            session.get.return_value = response({"data": [{"id": "remote-model"}]})
            session.post.return_value = completion(decision())
            with patch("scripts.evaluate_local_sentiment._analysis_text", side_effect=lambda **kw: "修正后：" + kw["title"]) as prepare, \
                    patch("scripts.evaluate_local_sentiment.LocalLlmRuntime") as runtime, patch("builtins.print"):
                report, directory = run(args, session)
            prepare.assert_called()
            self.assertEqual(prepare.call_count, 2)
            runtime.assert_not_called()
            self.assertEqual(source.read_bytes(), original_bytes)
            frozen = [json.loads(line) for line in (directory / "samples.jsonl").read_text().splitlines()]
            self.assertEqual(frozen, samples)
            self.assertEqual(digest(frozen), expected_hash)
            metadata = json.loads((directory / "metadata.json").read_text())
            self.assertEqual(metadata["dataset_sha256"], expected_hash)
            self.assertEqual(metadata["expected_dataset_sha256"], expected_hash)
            self.assertEqual(metadata["preprocessing_version"], ANALYSIS_TEXT_VERSION)
            self.assertEqual(metadata["input_mode"], "reprocess_title_and_content")
            self.assertEqual(metadata["historical_input_comparison"], {
                "available_count": 2, "changed_count": 1, "unchanged_count": 1, "missing_count": 0, "changed_ids": ["a"]})
            bodies = [call.kwargs["json"] for call in session.post.call_args_list]
            texts = [json.loads(body["messages"][1]["content"])["analysis_text"] for body in bodies]
            self.assertEqual(texts[:2], texts[2:])
            results = [json.loads(line) for line in (directory / "results.jsonl").read_text().splitlines()]
            self.assertEqual([r["historical_analysis_text"] for r in results],
                             ["旧标题片段", "修正后：原始完整标题"] * 2)
            self.assertEqual([r["input_changed_from_history"] for r in results], [True, False] * 2)
            self.assertEqual(report["comparison"]["left_variant"], "prompt")
            self.assertEqual(report["comparison"]["right_variant"], "refined")
            self.assertEqual(report["comparison"]["input_mismatch"], 0)
            self.assertEqual(set(report["variants"]), {"prompt", "refined"})

    def test_refined_does_not_change_existing_default_variants(self):
        self.assertEqual(VARIANTS["refined"], ("sentiment-v7.1", {"temperature": 0.1}))
        self.assertEqual(VARIANTS["prompt"], ("sentiment-v7", {"temperature": 0.1}))
        with tempfile.TemporaryDirectory() as tmp:
            session = Mock()
            session.get.return_value = response({"data": [{"id": "local"}]})
            session.post.return_value = completion(decision())
            args = parser().parse_args(["--smoke", "--limit", "1", "--output-dir", str(Path(tmp) / "run")])
            with patch("builtins.print"):
                report, _ = run(args, session)
            self.assertEqual(set(report["variants"]), {"baseline", "prompt"})


if __name__ == "__main__":
    unittest.main()
