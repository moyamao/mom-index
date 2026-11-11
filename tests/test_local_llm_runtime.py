import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from local_llm_runtime import LocalLlmRuntime


class LocalLlmRuntimeTest(unittest.TestCase):
    @patch("local_llm_runtime.ini_get_bool", return_value=False)
    def test_disabled_runtime_does_nothing(self, _):
        runtime = LocalLlmRuntime()
        with runtime:
            self.assertIsNone(runtime.process)

    @patch("local_llm_runtime.os.killpg")
    @patch("local_llm_runtime.subprocess.Popen")
    @patch("local_llm_runtime.shutil.which", return_value="/opt/homebrew/bin/llama-server")
    @patch("local_llm_runtime.Path.is_file", return_value=True)
    @patch("local_llm_runtime.LocalLlmRuntime._require_free_port")
    @patch("local_llm_runtime.LocalLlmRuntime._wait_ready", side_effect=RuntimeError("not ready"))
    @patch("local_llm_runtime.ini_get_int", side_effect=lambda section, key, default: default)
    @patch("local_llm_runtime.ini_get", side_effect=lambda section, key, default="": default)
    @patch("local_llm_runtime.ini_get_bool", return_value=True)
    def test_startup_failure_stops_owned_server(
        self, _, __, ___, wait_ready, require_port, is_file, which, popen, killpg
    ):
        process = Mock(pid=1234)
        process.poll.return_value = None
        popen.return_value = process

        runtime = LocalLlmRuntime()
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            runtime.__enter__()

        killpg.assert_called_once_with(1234, unittest.mock.ANY)
        process.wait.assert_called_once()
        self.assertIsNone(runtime._owner_lock)

    def test_no_proxy_adds_local_addresses_once(self):
        with patch.dict(os.environ, {"NO_PROXY": "example.com,127.0.0.1"}, clear=False):
            value = LocalLlmRuntime._no_proxy().split(",")
        self.assertEqual(value.count("127.0.0.1"), 1)
        self.assertEqual(value.count("localhost"), 1)

    @patch("local_llm_runtime.os.killpg")
    @patch("local_llm_runtime.subprocess.Popen")
    @patch("local_llm_runtime.shutil.which", return_value=None)
    @patch("local_llm_runtime.Path.is_file", return_value=True)
    @patch("local_llm_runtime.LocalLlmRuntime._require_free_port", side_effect=RuntimeError("occupied"))
    @patch("local_llm_runtime.LocalLlmRuntime._wait_ready", return_value="model.gguf")
    @patch("local_llm_runtime.ini_get_bool", return_value=True)
    def test_reused_server_is_not_started_or_stopped(self, enabled, ready, port, isfile, which, popen, killpg):
        with patch.dict(os.environ, {}, clear=False):
            with LocalLlmRuntime() as runtime:
                self.assertTrue(runtime.reused)
                self.assertIsNone(runtime.process)
                self.assertIsNone(runtime._owner_lock)
                self.assertEqual(os.environ["MOM_INDEX_LLM_MODEL"], "model.gguf")
            popen.assert_not_called()
            killpg.assert_not_called()
            which.assert_not_called()

    def test_owned_runtime_lock_blocks_other_jobs_until_released(self):
        first, second = LocalLlmRuntime(), LocalLlmRuntime()
        first._acquire_ownership_lock(65432)
        try:
            with self.assertRaisesRegex(RuntimeError, "按需模型任务正在运行"):
                second._acquire_ownership_lock(65432)
            self.assertIsNone(second._owner_lock)
        finally:
            first._release_ownership_lock()
        try:
            second._acquire_ownership_lock(65432)
            self.assertIsNotNone(second._owner_lock)
        finally:
            second._release_ownership_lock()

    @patch("local_llm_runtime.LocalLlmRuntime._stop_owned")
    def test_owned_runtime_exit_releases_lock(self, stop):
        runtime = LocalLlmRuntime()
        runtime.enabled = True
        runtime._acquire_ownership_lock(65432)
        runtime.__exit__(None, None, None)
        self.assertIsNone(runtime._owner_lock)
        stop.assert_called_once()

    @patch("local_llm_runtime.os.killpg")
    @patch("local_llm_runtime.subprocess.Popen")
    @patch("local_llm_runtime.shutil.which", return_value="/opt/homebrew/bin/llama-server")
    @patch("local_llm_runtime.Path.is_file", return_value=True)
    @patch("local_llm_runtime.LocalLlmRuntime._require_free_port", side_effect=RuntimeError("occupied"))
    @patch("local_llm_runtime.LocalLlmRuntime._wait_ready", side_effect=RuntimeError("模型身份不匹配"))
    @patch("local_llm_runtime.ini_get_bool", return_value=True)
    def test_mismatched_server_is_left_untouched(self, enabled, ready, port, isfile, which, popen, killpg):
        with self.assertRaisesRegex(RuntimeError, "身份不匹配"):
            with LocalLlmRuntime():
                self.fail("should never enter")
        popen.assert_not_called()
        killpg.assert_not_called()

    @patch("local_llm_runtime.json.load", return_value={"data": [{"id": "wrong.gguf"}]})
    @patch("local_llm_runtime.urllib.request.build_opener")
    def test_wait_ready_validates_identity_without_owned_process(self, opener, load):
        from unittest.mock import MagicMock
        opener.return_value.open.return_value = MagicMock()
        runtime = LocalLlmRuntime()
        with self.assertRaisesRegex(RuntimeError, "模型身份不匹配"):
            runtime._wait_ready(8080, Path("/models/expected.gguf"), 1)

    def test_reuse_can_be_disabled(self):
        with patch("local_llm_runtime.ini_get_bool", side_effect=lambda section, key, default: key == "enabled"), \
                patch("local_llm_runtime.Path.is_file", return_value=True), \
                patch("local_llm_runtime.shutil.which", return_value="/bin/llama-server"), \
                patch("local_llm_runtime.LocalLlmRuntime._require_free_port", side_effect=RuntimeError("occupied")), \
                patch("local_llm_runtime.LocalLlmRuntime._wait_ready") as ready:
            with self.assertRaisesRegex(RuntimeError, "occupied"):
                with LocalLlmRuntime():
                    self.fail("should never enter")
            ready.assert_not_called()


if __name__ == "__main__":
    unittest.main()
