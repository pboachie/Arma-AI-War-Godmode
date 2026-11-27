"""Check the retained game-only launcher without SteamCMD or a game server."""

import importlib.util
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch


class StandaloneLauncherTests(unittest.TestCase):
    def test_launcher_reaches_anonymous_update_and_start(self):
        launcher = Path(__file__).resolve().parents[1] / "docker" / "launch.py"
        spec = importlib.util.spec_from_file_location("_standalone_launcher", launcher)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        process = Mock(returncode=0)
        module.MAX_RESTARTS = "0"
        with patch.object(module.signal, "signal"), patch.object(module, "update_server") as update, \
                patch.object(module, "launch_server", return_value=process) as start, redirect_stdout(io.StringIO()) as log:
            with self.assertRaises(SystemExit) as exit_status:
                module.main()
        self.assertEqual(exit_status.exception.code, 0)
        self.assertIn("(anonymous)", log.getvalue())
        update.assert_called_once_with()
        start.assert_called_once_with()
        process.wait.assert_called_once_with()
