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
        self.assertEqual(result["game"]["mods"], [])
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
            {"ARMA_ADMIN_PASSWORD": (ADMIN * 2)[:33]},
            {"ARMA_GAME_PASSWORD": (RCON * 2)[:33]},
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

    def test_game_password_schema_limit_does_not_apply_to_rcon(self):
        admin, join, rcon = (ADMIN * 2)[:32], (RCON * 2)[:32], RCON * 2
        result = json.loads(self.generate(ARMA_ADMIN_PASSWORD=admin, ARMA_GAME_PASSWORD=join,
                                         ARMA_RCON_ENABLED="true", ARMA_RCON_PASSWORD=rcon).read_text())
        self.assertEqual(result["game"]["passwordAdmin"], admin)
        self.assertEqual(result["game"]["password"], join)
        self.assertEqual(result["rcon"]["password"], rcon)

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

    def test_workshop_pins_round_trip_in_order_without_changing_scenario(self):
        mods = [{"modId": "68EB7BF2F5940DD9", "name": "AI War - Core", "version": "1.0.6"},
                {"modId": "0123456789ABCDEF", "version": "2.1.0"}]
        mods_file = self.root / "workshop-mods.json"
        mods_file.write_text(json.dumps(mods), encoding="utf-8-sig")
        args = dict(output_path=self.output, values={"ARMA_ADMIN_PASSWORD": ADMIN}, mods_file=mods_file)
        for force in (False, True):
            game = json.loads(generate_config(**args, force=force).read_text(encoding="utf-8"))["game"]
            self.assertEqual(game["mods"], mods)
            self.assertEqual(game["scenarioId"], "{ECC61978EDCC2B5A}Missions/23_Campaign.conf")

    def test_invalid_workshop_pins_do_not_replace_existing_output(self):
        self.generate()
        original = self.output.read_bytes()
        mods_file = self.root / "workshop-mods.json"
        pin = {"modId": "68EB7BF2F5940DD9", "version": "1.0.6"}
        for value in (pin, [None], [{"modId": pin["modId"]}], [dict(pin, version="latest")],
                      [dict(pin, version=106)], [dict(pin, modId="invalid")], [dict(pin, name="\n")],
                      [dict(pin, unexpected=True)], [pin, dict(pin, modId=pin["modId"].lower())]):
            with self.subTest(value=value):
                mods_file.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ConfigError):
                    generate_config(output_path=self.output, values={"ARMA_ADMIN_PASSWORD": ADMIN},
                                    mods_file=mods_file, force=True)
                self.assertEqual(self.output.read_bytes(), original)

    def test_malformed_workshop_json_does_not_write_output(self):
        mods_file = self.root / "workshop-mods.json"
        mods_file.write_text("[{", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "valid UTF-8 JSON"):
            generate_config(output_path=self.output, values={"ARMA_ADMIN_PASSWORD": ADMIN}, mods_file=mods_file)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
