#!/usr/bin/env python3
"""Write a private Arma Reforger config from a safe, valid JSON template."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = PROJECT_ROOT / "Configs" / "ArmaServer" / "config.template.json"
OUTPUT_PATH = PROJECT_ROOT / "Arma Reforger Server" / "configs" / "config.json"
ENV_KEYS = frozenset({
    "ARMA_ADMIN_PASSWORD", "ARMA_GAME_PASSWORD", "ARMA_SERVER_NAME",
    "ARMA_SCENARIO_ID", "ARMA_MAX_PLAYERS", "ARMA_GAME_VISIBLE",
    "ARMA_GAME_BIND_ADDRESS", "ARMA_GAME_PORT", "ARMA_PUBLIC_ADDRESS", "ARMA_PUBLIC_PORT",
    "ARMA_A2S_BIND_ADDRESS", "ARMA_A2S_PORT", "ARMA_RCON_ENABLED",
    "ARMA_RCON_BIND_ADDRESS", "ARMA_RCON_PORT", "ARMA_RCON_PASSWORD", "ARMA_RCON_PERMISSION",
})


class ConfigError(ValueError):
    """Invalid operator input. Messages must not include setting values."""


def read_env_file(path: Path) -> dict[str, str]:
    """Read literal ARMA_* assignments, with optional outer quotes and no expansion."""
    values: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, equals, raw = line.partition("=")
        key = key.strip()
        if key not in ENV_KEYS:
            continue
        value = raw.strip()
        if not equals or key in values:
            raise ConfigError(f"Expected one {key}=VALUE assignment (line {lineno})")
        if value.startswith(("\"", "'")):
            if len(value) < 2 or not value.endswith(value[0]):
                raise ConfigError(f"Unclosed quote for {key} (line {lineno})")
            value = value[1:-1]
        values[key] = value
    return values


def _integer(value: str, key: str, lower: int, upper: int) -> int:
    if not re.fullmatch(r"[0-9]{1,5}", value) or not lower <= int(value) <= upper:
        raise ConfigError(f"{key} must be a whole number from {lower} to {upper}")
    return int(value)


def _boolean(value: str, key: str) -> bool:
    if value.lower() not in {"true", "false"}:
        raise ConfigError(f"{key} must be true or false")
    return value.lower() == "true"


def _address(value: str, key: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(value)
    except ValueError:
        raise ConfigError(f"{key} must be an IPv4 address") from None


def _password(value: str, key: str) -> str:
    if (not re.fullmatch(r"[!-~]{20,128}", value) or len(set(value)) < 8
            or value.lower().startswith(("changeme", "choose-", "replace-", "your-"))):
        raise ConfigError(f"{key} needs a unique random password of 20–128 printable ASCII characters without spaces")
    return value


def _scenario(value: str, vanilla: str) -> str:
    if value.upper() == vanilla[1:17]:
        return vanilla  # The old sample stored only the vanilla scenario GUID.
    match = re.fullmatch(r"\{[0-9A-Fa-f]{16}\}([A-Za-z0-9_ ./-]+\.conf)", value)
    if not match or any(part in {"", ".", ".."} for part in match[1].split("/")):
        raise ConfigError("ARMA_SCENARIO_ID must be the full scenario ID from -listScenarios, including its .conf path")
    return value


def read_mods_file(path: Path) -> list[dict[str, str]]:
    """Read an explicit list of Workshop version pins, in load order."""
    try:
        mods = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("Workshop mods file must be valid UTF-8 JSON") from exc
    if not isinstance(mods, list):
        raise ConfigError("Workshop mods file must contain a JSON array")
    seen: set[str] = set()
    for index, mod in enumerate(mods, 1):
        if not isinstance(mod, dict) or mod.keys() - {"modId", "name", "version"}:
            raise ConfigError(f"Workshop mod entry {index} must contain modId, version, and optionally name")
        mod_id, version = mod.get("modId"), mod.get("version")
        if not isinstance(mod_id, str) or not re.fullmatch(r"[0-9A-Fa-f]{16}", mod_id):
            raise ConfigError(f"Workshop mod entry {index} needs a 16-digit hexadecimal modId")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ConfigError(f"Workshop mod entry {index} needs an exact numeric major.minor.patch version")
        name = mod.get("name")
        if "name" in mod and (not isinstance(name, str) or not name.strip() or not name.isprintable()):
            raise ConfigError(f"Workshop mod entry {index} has an invalid name")
        if mod_id.upper() in seen:
            raise ConfigError(f"Workshop mod entry {index} repeats a modId")
        seen.add(mod_id.upper())
    return mods


def _render(config: dict, values: Mapping[str, str]) -> dict:
    game, a2s = config["game"], config["a2s"]
    get = values.get
    admin = _password(get("ARMA_ADMIN_PASSWORD", ""), "ARMA_ADMIN_PASSWORD")
    join = get("ARMA_GAME_PASSWORD", "")
    if join:
        _password(join, "ARMA_GAME_PASSWORD")
        if join == admin:
            raise ConfigError("ARMA_GAME_PASSWORD and ARMA_ADMIN_PASSWORD must differ")
    name = get("ARMA_SERVER_NAME", game["name"])
    if not 1 <= len(name) <= 100 or not name.isprintable():
        raise ConfigError("ARMA_SERVER_NAME must have 1–100 printable characters")

    game_bind = _address(get("ARMA_GAME_BIND_ADDRESS", config["bindAddress"]), "ARMA_GAME_BIND_ADDRESS")
    game_port = _integer(get("ARMA_GAME_PORT", str(config["bindPort"])), "ARMA_GAME_PORT", 1, 65535)
    visible = _boolean(get("ARMA_GAME_VISIBLE", str(game["visible"])), "ARMA_GAME_VISIBLE")
    if visible and game_bind.is_loopback:
        raise ConfigError("A listed server needs a non-loopback ARMA_GAME_BIND_ADDRESS")
    config.update(bindAddress=str(game_bind), bindPort=game_port,
                  publicPort=_integer(get("ARMA_PUBLIC_PORT", str(game_port)), "ARMA_PUBLIC_PORT", 1, 65535))
    public_address = get("ARMA_PUBLIC_ADDRESS", "")
    if public_address:
        config["publicAddress"] = str(_address(public_address, "ARMA_PUBLIC_ADDRESS"))
    a2s_bind = _address(get("ARMA_A2S_BIND_ADDRESS", a2s["address"]), "ARMA_A2S_BIND_ADDRESS")
    a2s_port = _integer(get("ARMA_A2S_PORT", str(a2s["port"])), "ARMA_A2S_PORT", 1, 65535)
    a2s.update(address=str(a2s_bind), port=a2s_port)
    game.update(name=name, password=join, passwordAdmin=admin, visible=visible,
                scenarioId=_scenario(get("ARMA_SCENARIO_ID", game["scenarioId"]), game["scenarioId"]),
                maxPlayers=_integer(get("ARMA_MAX_PLAYERS", str(game["maxPlayers"])), "ARMA_MAX_PLAYERS", 1, 128))

    endpoints = [(game_bind, game_port), (a2s_bind, a2s_port)]
    config.pop("rcon", None)
    if _boolean(get("ARMA_RCON_ENABLED", "false"), "ARMA_RCON_ENABLED"):
        rcon_bind = _address(get("ARMA_RCON_BIND_ADDRESS", "127.0.0.1"), "ARMA_RCON_BIND_ADDRESS")
        if not rcon_bind.is_loopback:
            raise ConfigError("ARMA_RCON_BIND_ADDRESS must be loopback")
        rcon_pass = _password(get("ARMA_RCON_PASSWORD", ""), "ARMA_RCON_PASSWORD")
        if rcon_pass in {admin, join}:
            raise ConfigError("ARMA_RCON_PASSWORD must differ from the game and admin passwords")
        rcon_port = _integer(get("ARMA_RCON_PORT", "19999"), "ARMA_RCON_PORT", 1, 65535)
        permission = get("ARMA_RCON_PERMISSION", "monitor")
        if permission not in {"monitor", "admin"}:
            raise ConfigError("ARMA_RCON_PERMISSION must be monitor or admin")
        config["rcon"] = {"address": str(rcon_bind), "port": rcon_port, "password": rcon_pass,
                          "maxClients": 2, "permission": permission}
        endpoints.append((rcon_bind, rcon_port))
    for i, (address, port) in enumerate(endpoints):
        if any(port == other_port and (address == other or address.is_unspecified or other.is_unspecified)
               for other, other_port in endpoints[i + 1:]):
            raise ConfigError("Game, A2S, and RCON must not share a UDP endpoint")
    return config


def generate_config(template_path: Path = TEMPLATE_PATH, output_path: Path = OUTPUT_PATH, *,
                    values: Mapping[str, str] | None = None, force: bool = False,
                    mods_file: Path | None = None) -> Path:
    """Validate and atomically create the output; no replacement without force."""
    config = _render(json.loads(template_path.read_text(encoding="utf-8")),
                     os.environ if values is None else values)
    if mods_file is not None:
        config["game"]["mods"] = read_mods_file(mods_file)
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output_path.is_symlink():
        raise ConfigError("Refusing to write config to a symbolic link")
    if output_path.exists() and not force:
        raise ConfigError("Output already exists. Back it up or select a new path (use --force to replace it)")

    # mkstemp creates the file with mode 0600 on POSIX. Being in the same
    # directory keeps the link/rename atomic and avoids following the output path.
    fd, name = tempfile.mkstemp(prefix=".arma-config-", dir=output_path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(config, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        if force:
            staged.replace(output_path)
        else:
            try:
                os.link(staged, output_path)
            except FileExistsError:
                raise ConfigError("Output already exists. Use a different path or --force") from None
    finally:
        staged.unlink(missing_ok=True)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Private Arma settings file (otherwise use exported ARMA_* settings)")
    parser.add_argument("--mods-file", type=Path, help="JSON array of exact Workshop modId/version pins")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Destination config.json")
    parser.add_argument("--force", action="store_true", help="Replace an existing config; back it up first")
    args = parser.parse_args()
    try:
        values = read_env_file(args.env_file) if args.env_file is not None else {}
        values.update({key: os.environ[key] for key in ENV_KEYS if key in os.environ})
        out = generate_config(output_path=args.output, values=values, force=args.force, mods_file=args.mods_file)
    except (ConfigError, OSError) as exc:
        parser.exit(1, f"Config not written: {exc}\n")
    print(f"Config generated: {out}")


if __name__ == "__main__":
    main()
