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

Generate the dedicated-server config as in [Server setup](SERVER_SETUP.md#2-generate-a-private-configuration). Set `ARMA_SCENARIO_ID` to your full mission-header resource ID before generation or set `game.scenarioId` directly in the private output. For a Workshop build, add the exact trusted Workshop `modId` and `version` to `game.mods`. An addon ID identifies the build to load. A scenario ID identifies the mission to run. Confirm both against the dedicated-server startup log. [Bohemia Server Config reference](https://community.bohemia.net/wiki/Arma_Reforger:Server_Config)

## 3. Install the game key on the server only

1. On the VPS, open `middleware/.env` privately and retrieve `GODMODE_GAME_API_KEY`. It must be distinct from `GODMODE_API_KEY`, which is only for administration.
2. In the directory passed to the dedicated server's `-profile` option, create `godmode_api_key.txt` as plain text with the game key on one line and no quotes or byte-order mark. For the [server setup example](SERVER_SETUP.md#3-launch-vanilla-first) this is `"$arma_profile/godmode_api_key.txt"`. Use a private editor, restrict the profile directory to the game service account, and make the file readable only by that account (mode `0600`).
3. The game bridge reads `$profile:godmode_api_key.txt` and uses the value in the `X-API-Key` header for its middleware requests. Never put the key in a Workbench property, script, addon package, Workshop upload, client install, or URL. The game process does not need the admin or provider keys.

## 4. Verify a private match

Start the middleware, then the dedicated server with the chosen config and profile. Check the server log for the expected addon, scenario, and `[GodMode]` or `[GodMode:Hook]` initialization. Check for an accepted game request in the middleware's logs or authenticated dashboard. A startup line alone does not establish that a request succeeded.

Join with a matching game client on an approved private network path. Check spawn and basic scenario play. Enable a provider only after the bridge works, then confirm an accepted report, a valid command response, and the expected in-game effect. Record the game and Workbench builds, addon version, scenario resource ID, and resulting checks without including credentials. Treat any untested scenario, feature, or version as unverified. [Troubleshooting](OPERATIONS.md#troubleshooting)
