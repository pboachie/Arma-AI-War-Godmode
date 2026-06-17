#!/usr/bin/env python3
"""Render Arma Reforger server config from a template and environment variables.

Reads ``config.template.json``, replaces ``__PLACEHOLDER__`` tokens with values
from environment variables (or sensible defaults), and writes ``config.json``.

Usage::

    python scripts/generate_config.py                # uses .env in middleware/
    ARMA_ADMIN_PASSWORD=secret python scripts/generate_config.py
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolve project paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
TEMPLATE_PATH = PROJECT_ROOT / "Configs" / "ArmaServer" / "config.template.json"
OUTPUT_PATH = PROJECT_ROOT / "Arma Reforger Server" / "configs" / "config.json"
DOTENV_PATH = PROJECT_ROOT / "middleware" / ".env"

# ---------------------------------------------------------------------------
# Placeholder -> (env-var name, default value)
# ---------------------------------------------------------------------------
PLACEHOLDER_MAP: dict[str, tuple[str, str]] = {
    "__ADMIN_PASSWORD__": ("ARMA_ADMIN_PASSWORD", "changeme"),
    "__RCON_PASSWORD__": ("ARMA_RCON_PASSWORD", "changeme_rcon"),
    "__RCON_ADDRESS__": ("ARMA_RCON_ADDRESS", "0.0.0.0"),
    "__RCON_PORT__": ("ARMA_RCON_PORT", "19999"),
    "__GAME_PORT__": ("ARMA_GAME_PORT", "2001"),
    "__A2S_PORT__": ("ARMA_A2S_PORT", "17777"),
    "__MAX_PLAYERS__": ("ARMA_MAX_PLAYERS", "124"),
    "__SERVER_NAME__": ("ARMA_SERVER_NAME", "AI War [100% LLM-Managed]"),
    "__SCENARIO_ID__": ("ARMA_SCENARIO_ID", "ECC61978EDCC2B5A"),
}


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader -- sets os.environ for KEY=VALUE lines."""
    if not path.is_file():
        logger.debug(".env file not found at %s, skipping", path)
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Do not overwrite variables already set in the environment
            if key not in os.environ:
                os.environ[key] = value


def _resolve(placeholder: str) -> str:
    """Return the replacement value for *placeholder*."""
    env_var, default = PLACEHOLDER_MAP[placeholder]
    return os.environ.get(env_var, default)


def generate_config(
    template_path: Path = TEMPLATE_PATH,
    output_path: Path = OUTPUT_PATH,
) -> Path:
    """Read the template, substitute placeholders, and write config.json."""
    logger.info("Reading template: %s", template_path)
    raw = template_path.read_text(encoding="utf-8")

    # Replace each placeholder token.  Numeric placeholders (ports, counts)
    # appear *without* quotes in the template so the output stays valid JSON.
    for placeholder in PLACEHOLDER_MAP:
        value = _resolve(placeholder)
        raw = raw.replace(placeholder, value)

    # Sanity: make sure no unreplaced placeholders remain
    remaining = re.findall(r"__[A-Z_]+__", raw)
    if remaining:
        logger.warning("Unreplaced placeholders in output: %s", remaining)

    # Validate JSON before writing
    parsed = json.loads(raw)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(parsed, fh, indent=2)
        fh.write("\n")

    logger.info("Wrote config: %s", output_path)
    return output_path


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    _load_dotenv(DOTENV_PATH)
    out = generate_config()
    print(f"Config generated: {out}")


if __name__ == "__main__":
    main()
