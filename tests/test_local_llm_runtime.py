import os
import unittest
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

    def test_no_proxy_adds_local_addresses_once(self):
        with patch.dict(os.environ, {"NO_PROXY": "example.com,127.0.0.1"}, clear=False):
            value = LocalLlmRuntime._no_proxy().split(",")
        self.assertEqual(value.count("127.0.0.1"), 1)
        self.assertEqual(value.count("localhost"), 1)


if __name__ == "__main__":
    unittest.main()
