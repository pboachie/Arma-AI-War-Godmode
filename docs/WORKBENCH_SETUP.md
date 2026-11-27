# Build and connect the game addon

This is a source integration workflow. The checked-in Western Everon world and layer files are reference material, not a tested scenario or a ready-to-install addon. Plan to compile and validate the addon for your exact Arma Reforger build. Start with a private test server and an operator who can stop it.

## 1. Import and compile

1. Open Arma Reforger Tools on a Workbench machine with the matching Reforger data. Create a mod project with Arma Reforger as a dependency. Keep the project separate from the game installation and from the server profile. [Bohemia Mod Project Setup](https://community.bohemia.net/wiki/Arma_Reforger:Mod_Project_Setup)
2. Copy the `.c` sources from [`scripts/Game/GameMode/`](../scripts/Game/GameMode/) into your mod's `scripts/Game/GameMode/`. In Workbench, check that the project includes `scripts/Game` in the Game script module. Refresh the project and compile in Script Editor (F7). Fix missing dependencies or API mismatches before proceeding. [Bohemia Scripting Example](https://community.bohemia.net/wiki/Arma_Reforger:Scripting_Example)
3. For an explicit integration, open a private test world in World Editor, select its actual GameMode entity, and add `GM_GodModeComponentClass`. Save the world and its layer. Confirm there is only one active Godmode controller. `GM_ConflictHook.c` also hooks the base Conflict game mode when loaded. If you only want the explicit component path for a test, leave that hook out of the test addon. Do not assume loading the addon has no effect on stock Conflict.
4. Set the component's Server URL to `http://127.0.0.1:8080` for a dedicated server running directly on the same host as the middleware. Keep the component's middleware communication off for the first editor-only test. The bridge runs only in a dedicated-server session. When testing it, enable communication in the scenario but keep `LLM_PROVIDER=disabled` in the middleware so you can check the connection without paid requests. [Bohemia World Editor](https://community.bohemia.net/wiki/Arma_Reforger:World_Editor)
5. Confirm that referenced prefabs, sounds, and effects resolve in the current Resource Browser and Audio Editor. Replace placeholder or stale paths with resources from your game version. [`gm_config.json`](../Configs/GodMode/gm_config.json) is a reference for the integration, not a substitute for setting the component in Workbench.
6. Create or update the `SCR_MissionHeader` `.conf` to reference your world. Save it under your mod's `Missions/` directory, preview the scenario in Workbench, and record the header's full resource ID. That ID, not the world `.ent` path, is used as the dedicated server's `game.scenarioId`. [Bohemia scenario header example](https://community.bohemia.net/wiki/Arma_Reforger:Capture_%26_Hold_Setup#Scenario_Header)

## 2. Load only a trusted build

Bundle your reviewed addon with Workbench. If distributing it through Workshop, use a test release and confirm the intended game branch, publisher, addon ID, version, and dependencies. Install the same reviewed build on the dedicated server and participating clients. Do not trust an addon based only on its display name. [Bohemia Mod Publishing Process](https://community.bohemia.net/wiki/Arma_Reforger:Mod_Publishing_Process)

Generate the dedicated-server config as in [Server setup](SERVER_SETUP.md#2-generate-a-private-configuration). For a Workshop build, save a separate JSON array of exact pins as `workshop-mods.json` in your private configuration directory. The following is a pin for the existing AI War - Core release, not a recommendation to run an AI-powered match with it.

```json
[
  {"modId": "68EB7BF2F5940DD9", "name": "AI War - Core", "version": "1.0.6"}
]
```

```bash
python3 scripts/generate_config.py --env-file "$arma_private/arma.env" \
  --mods-file "$arma_private/workshop-mods.json" --output "$arma_private/config.json"
```

`--mods-file` sets `game.mods` in file order. Use the same reviewed file when regenerating the config. Include any required Workshop dependencies at their reviewed versions. Set `ARMA_SCENARIO_ID` separately if the addon supplies a compatible custom mission. An addon ID identifies the build to load. It is not a scenario ID. The `-listScenarios` output for the loaded addons and the startup log are the checks for the full mission `.conf` resource ID and the installed addon versions. [Bohemia Server Config reference](https://community.bohemia.net/wiki/Arma_Reforger:Server_Config)

**Published AI War - Core 1.0.6:** The Workshop listing reports no selectable scenarios for this version. The published version predates the server-profile game key and does not send the header required by this middleware. Installing `godmode_api_key.txt` does not add that capability to the packaged addon. You can test the pin privately alongside a supported stock scenario, but loading the addon is not a working AI game bridge. Use a compatible, reviewed addon build for the authenticated test below. Do not disable authentication or expose the middleware to work around a version mismatch. [Workshop listing](https://reforger.armaplatform.com/workshop/68EB7BF2F5940DD9)

## 3. Install the game key on the server only

1. On the VPS, open `middleware/.env` privately and retrieve `GODMODE_GAME_API_KEY`. It must be distinct from `GODMODE_API_KEY`, which is only for administration.
2. In the directory passed to the dedicated server's `-profile` option, create `godmode_api_key.txt` as plain text with the game key on one line and no quotes or byte-order mark. For the [server setup example](SERVER_SETUP.md#3-launch-vanilla-first) this is `"$arma_profile/godmode_api_key.txt"`. Use a private editor, restrict the profile directory to the game service account, and make the file readable only by that account (mode `0600`).
3. The game bridge reads `$profile:godmode_api_key.txt` and uses the value in the `X-API-Key` header for its middleware requests. Never put the key in a Workbench property, script, addon package, Workshop upload, client install, or URL. The game process does not need the admin or provider keys.

## 4. Verify a private match

Start the middleware, then the dedicated server with the chosen config and profile. Check the server log for the expected addon, scenario, and `[GodMode]` or `[GodMode:Hook]` initialization. Check for an accepted game request in the middleware's logs or authenticated dashboard. A startup line alone does not establish that a request succeeded.

Join with a matching game client on an approved private network path. Check spawn and basic scenario play. Enable a provider only after the bridge works, then confirm an accepted report, a valid command response, and the expected in-game effect. Record the game and Workbench builds, addon version, scenario resource ID, and resulting checks without including credentials. Treat any untested scenario, feature, or version as unverified. [Troubleshooting](OPERATIONS.md#troubleshooting)
