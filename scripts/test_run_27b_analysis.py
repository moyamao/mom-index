import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import run_27b_analysis as launcher


class LauncherTests(unittest.TestCase):
    def exercise(self, task_exit=0, ready_error=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / 'model.gguf'
            model.touch()
            args = argparse.Namespace(model=model,llama_server='llama-server',port=8080,
                context=4096,python='python3',days=7,limit=10,startup_timeout=1,
                reanalyze_all=False,dry_run=False)
            server = Mock(pid=12345)
            task = Mock(pid=12346)
            task.wait.return_value = task_exit
            cloud_task = Mock(pid=12347)
            cloud_task.wait.return_value = 0
            with patch.object(launcher,'ROOT',root), patch.object(launcher.shutil,'which',return_value='/llama-server'), \
                 patch.object(launcher,'require_free_port'), patch.object(launcher.subprocess,'Popen',side_effect=[server,task,cloud_task]) as popen, \
                 patch.object(launcher,'wait_ready',side_effect=ready_error,return_value=str(model)), \
                 patch.object(launcher,'stop_owned') as stop:
                if ready_error:
                    with self.assertRaises(RuntimeError):
                        launcher.run(args)
                    self.assertEqual(popen.call_count,1)
                else:
                    self.assertEqual(launcher.run(args),task_exit)
                    self.assertTrue(popen.call_args.kwargs['start_new_session'])
                    env=popen.call_args.kwargs['env']
                    self.assertEqual(env['MOM_INDEX_LLM_MODEL'],str(model))
                    self.assertEqual(env['CONSENSUS_27B_MODEL'],str(model))
                    self.assertEqual(env['MOM_INDEX_RUNTIME_ROLE'],'analyst')
                    stop.assert_any_call(cloud_task if task_exit == 0 else task)
                stop.assert_any_call(server)

    def test_normal_completion_closes_owned_processes(self):
        self.exercise()

    def test_analysis_failure_closes_server_and_returns_failure(self):
        self.exercise(task_exit=1)

    def test_startup_failure_never_starts_analysis(self):
        self.exercise(ready_error=RuntimeError('startup timeout'))

    def test_occupied_port_is_not_killed(self):
        with patch.object(launcher.socket,'socket') as socket:
            socket.return_value.__enter__.return_value.bind.side_effect=OSError('busy')
            with self.assertRaisesRegex(RuntimeError,'已被占用'):
                launcher.require_free_port(8080)

    def test_shutdown_uses_only_owned_group(self):
        process=Mock(pid=12345)
        process.poll.return_value=None
        with patch.object(launcher.os,'killpg') as kill:
            launcher.stop_owned(process)
            kill.assert_called_once_with(12345,launcher.signal.SIGTERM)

    def test_python_selection_skips_active_environment_without_dependencies(self):
        with patch.object(launcher, 'ROOT', Path('/project')), \
             patch.object(launcher.sys, 'executable', '/bad/venv/python3'), \
             patch.object(Path, 'is_file', return_value=True), \
             patch.object(launcher, '_python_dependency_error', side_effect=['missing requests', 'missing requests', '']):
            self.assertEqual(launcher.select_python(), '/usr/bin/python3')

    def test_explicit_python_with_missing_dependencies_fails_early(self):
        with patch.object(launcher.shutil, 'which', return_value='/custom/python3'), \
             patch.object(launcher, '_python_dependency_error', return_value='missing requests'):
            with self.assertRaisesRegex(RuntimeError, '缺少分析依赖'):
                launcher.select_python('python3')


if __name__=='__main__':
    unittest.main()
