import json
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

import pipeline
from analyzer import index_calculator
from analyzer.llm_analyzer import AnalysisResult
from storage import pending_run, mysql_store


class CollectorRecoveryTest(unittest.TestCase):
    def test_write_failure_preserves_payload_not_dashboard_then_retry_is_idempotent(self):
        post = {"id": "a", "platform": "weibo", "title": "黄金", "published_at": "2026-10-16 00:01:00"}
        item = AnalysisResult("a", "黄金", "weibo", "gold", sentiment_source="llm")
        posts = {"gold": [post]}
        with TemporaryDirectory() as d, ExitStack() as stack:
            def mock(name, **kw):
                return stack.enter_context(patch("pipeline." + name, **kw))
            stack.enter_context(patch.object(pending_run, "PENDING", Path(d) / "private"))
            mock("_assert_collection_role")
            mock("mysql_enabled", return_value=True)
            mock("_should_reuse_today_collection", return_value=False)
            mock("_collect_posts", return_value=posts)
            mock("beijing_now", return_value=datetime(2026, 10, 16, 1))
            mock("filter_posts_pending_analysis", return_value=(posts, {"pending": 1, "duplicates": 0, "already_analyzed": 0}))
            mock("local_llm_for_analysis")
            mock("analyze_all", return_value={"gold": [item]})
            mock("get_dashboard_data", return_value={"latest": None, "record_count": 0})
            mock("persist_pipeline_run", side_effect=OSError("lost connection"))
            history = mock("add_record")
            with self.assertRaisesRegex(RuntimeError, "本次不发布"):
                pipeline.run_pipeline()
            history.assert_not_called()
            path, = pending_run.PENDING.glob("*.json")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            job = json.loads(path.read_text())
            self.assertEqual(job["dashboard"]["latest"]["date"], "2026-10-15")
            self.assertEqual(job["results"]["gold"][0]["post_id"], "a")
            conn = MagicMock()
            cur = conn.cursor.return_value.__enter__.return_value
            stack.enter_context(patch.object(mysql_store, "mysql_enabled", return_value=True))
            stack.enter_context(patch.object(mysql_store, "_connect", return_value=conn))
            stack.enter_context(patch.object(mysql_store, "_ensure_tables"))
            write = stack.enter_context(patch.object(mysql_store, "persist_pipeline_run", side_effect=OSError("failed")))
            cur.fetchone.return_value = None
            with self.assertRaises(OSError):
                pending_run.replay(path)
            self.assertTrue(path.exists())
            self.assertEqual(write.call_args.args[-1], job["note"])
            # Models ran once; a lost acknowledgement is checked against the original token.
            cur.fetchone.return_value = {"id": 10}
            pending_run.replay(path)
            self.assertEqual(write.call_count, 1)
            self.assertFalse(path.exists())

    def test_short_history_stays_real_even_on_empty_rerun(self):
        when = datetime(2026, 10, 16, 1)
        item = AnalysisResult("p", "t", "weibo", "gold", source_datetime="2026-10-16 00:01:00")
        with TemporaryDirectory() as d, patch.object(index_calculator, "DATA_DIR", d):
            index_calculator.add_record({}, {"gold": [item]}, when)
            index_calculator.add_record({}, {}, when)
            records = index_calculator.load_history()["records"]
            self.assertEqual([r["date"] for r in records], ["2026-10-15"])
            self.assertEqual(records[0]["sectors"]["gold"]["details"]["total_posts"], 1)

    def test_checkpoints_are_durable_before_next_post_and_share_batch_id(self):
        connection = MagicMock()
        cur = connection.cursor.return_value.__enter__.return_value
        cur.lastrowid = 41
        item = AnalysisResult("p", "t", "xueqiu", "gold", sentiment_source="llm")
        with patch.object(mysql_store, "_connect", return_value=connection), patch.object(mysql_store, "_ensure_tables"):
            with self.assertRaises(KeyboardInterrupt):
                with mysql_store.checkpointed_analysis_batch(2, "test") as (batch, save):
                    save({"id": "p", "run_id": 123}, item)
                    self.assertEqual(batch, 41)
                    self.assertEqual(connection.commit.call_count, 2)
                    self.assertEqual(cur.execute.call_args.args[1], ("partial", None, 1, 0, batch))
                    raise KeyboardInterrupt
            connection.rollback.assert_called_once()
            with mysql_store.checkpointed_analysis_batch(1, "resume") as (batch, save):
                save({"id": "q", "run_id": 123}, AnalysisResult("q", "t", "xueqiu", "gold"))
                self.assertEqual(cur.execute.call_args.args[1][0], "completed")
