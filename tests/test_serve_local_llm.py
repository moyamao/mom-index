import unittest
from unittest.mock import patch

from scripts.serve_local_llm import server_command


class ServeLocalModelTests(unittest.TestCase):
    def test_model_and_port_come_from_this_machines_config(self):
        values = {"model_path": "/models/current.gguf", "llama_server": "/opt/bin/llama-server"}
        with patch("scripts.serve_local_llm.ini_get", side_effect=lambda s, k, d: values.get(k, d)), \
                patch("scripts.serve_local_llm.ini_get_int", side_effect=lambda s, k, d: {"port": 8090, "context": 8192}.get(k, d)), \
                patch("scripts.serve_local_llm.Path.is_file", return_value=True), \
                patch("scripts.serve_local_llm.shutil.which", return_value="/opt/bin/llama-server"), \
                patch("scripts.serve_local_llm.LocalLlmRuntime._require_free_port") as free:
            command = server_command("0.0.0.0")
        self.assertEqual(command[command.index("-m") + 1], "/models/current.gguf")
        self.assertEqual(command[command.index("--host") + 1], "0.0.0.0")
        self.assertEqual(command[command.index("--port") + 1], "8090")
        self.assertEqual(command[command.index("-c") + 1], "8192")
        free.assert_called_once_with(8090)

    def test_missing_model_config_stops(self):
        with patch("scripts.serve_local_llm.ini_get", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "model_path"):
                server_command("127.0.0.1")


if __name__ == "__main__":
    unittest.main()
