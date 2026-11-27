import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from scripts.generate_config import ConfigError, generate_config, read_env_file


# Public test inputs only. A quote and a dollar sign intentionally exercise
# literal handling and JSON encoding for otherwise ordinary configuration.
ADMIN = 'fixture_HZ82uQ9H$"gJ6WvR0e'
RCON = 'fixture_w13aMC6vQkTzUdX9J'


class GenerateConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.output = self.root / "configs" / "config.json"

    def generate(self, **overrides):
        return generate_config(output_path=self.output, values={"ARMA_ADMIN_PASSWORD": ADMIN, **overrides})

    def test_private_defaults(self):
        result = json.loads(self.generate().read_text(encoding="utf-8"))
        self.assertEqual(result["bindAddress"], "127.0.0.1")
        self.assertEqual(result["a2s"]["address"], "127.0.0.1")
        self.assertEqual(result["game"]["passwordAdmin"], ADMIN)
        self.assertEqual(result["game"]["scenarioId"], "{ECC61978EDCC2B5A}Missions/23_Campaign.conf")
        self.assertTrue(result["game"]["gameProperties"]["battlEye"])
        self.assertFalse(result["game"]["visible"])
        self.assertNotIn("rcon", result)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)

    def test_rcon_is_explicit_and_monitor_only_by_default(self):
        result = json.loads(self.generate(ARMA_RCON_ENABLED="true", ARMA_RCON_PASSWORD=RCON).read_text(encoding="utf-8"))
        self.assertEqual(result["rcon"]["address"], "127.0.0.1")
        self.assertEqual(result["rcon"]["permission"], "monitor")

    def test_refuses_invalid_values_before_writing(self):
        for options in (
            {"ARMA_ADMIN_PASSWORD": ""},
            {"ARMA_ADMIN_PASSWORD": "changeme"},
            {"ARMA_RCON_ENABLED": "true", "ARMA_RCON_PASSWORD": ADMIN},
            {"ARMA_RCON_ENABLED": "true", "ARMA_RCON_PASSWORD": RCON, "ARMA_RCON_BIND_ADDRESS": "0.0.0.0"},
            {"ARMA_GAME_PORT": "0"},
            {"ARMA_GAME_PORT": "17777"},
            {"ARMA_GAME_VISIBLE": "true"},
            {"ARMA_SCENARIO_ID": "{0123456789ABCDEF}Missions/Test.ent"},
        ):
            with self.subTest(options=tuple(options)), self.assertRaises(ConfigError):
                self.generate(**options)
            self.assertFalse(self.output.exists())

    def test_replacement_requires_force(self):
        self.output.parent.mkdir()
        original = b'{"operator_edits":true}\n'
        self.output.write_bytes(original)
        with self.assertRaises(ConfigError):
            self.generate()
        self.assertEqual(self.output.read_bytes(), original)
        generate_config(output_path=self.output, values={"ARMA_ADMIN_PASSWORD": ADMIN}, force=True)
        self.assertNotEqual(self.output.read_bytes(), original)
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    @unittest.skipUnless(os.name == "posix", "requires symlink privileges")
    def test_refuses_output_symlink(self):
        target = self.root / "untouched"
        target.write_bytes(b"original\n")
        self.output.parent.mkdir()
        self.output.symlink_to(target)
        with self.assertRaises(ConfigError):
            generate_config(output_path=self.output, values={"ARMA_ADMIN_PASSWORD": ADMIN}, force=True)
        self.assertEqual(target.read_bytes(), b"original\n")

    def test_literal_env_file_preserves_value_and_ignores_unrelated_keys(self):
        env = self.root / "arma.env"
        env.write_text(f"# Private settings\nARMA_ADMIN_PASSWORD='{ADMIN}'\nLLM_PROVIDER=disabled\n", encoding="utf-8")
        self.assertEqual(read_env_file(env), {"ARMA_ADMIN_PASSWORD": ADMIN})


if __name__ == "__main__":
    unittest.main()
