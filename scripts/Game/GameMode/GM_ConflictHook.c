// GM_ConflictHook.c — Hooks into base-game Conflict GameMode to bootstrap GodMode
// Uses Enfusion's "modded class" to inject the AI War system at runtime.
// NOTE: This class extends SCR_GameModeCampaign, NOT GM_GodModeComponent.
// Sub-manager SetOwner() calls are skipped because they require GM_GodModeComponent.
// The managers still function — they just cannot call back to LogEvent/GetMissionTime.
// All m_pOwner usages in sub-managers are null-guarded.

modded class SCR_GameModeCampaign : SCR_BaseGameMode
{
    protected ref GM_RestClient m_pGM_RestClient;
    protected ref GM_WeatherController m_pGM_WeatherCtrl;
    protected ref GM_SquadManager m_pGM_SquadMgr;
    protected ref GM_VehicleManager m_pGM_VehicleMgr;
    protected ref GM_CleanupSystem m_pGM_CleanupSys;
    protected ref GM_SoundController m_pGM_SoundCtrl;
    protected ref GM_ParticleController m_pGM_ParticleCtrl;
    protected ref GM_NarrativeManager m_pGM_NarrativeMgr;
    protected ref GM_MissionEngine m_pGM_MissionEngine;
    protected ref GM_AmbientBattlefield m_pGM_AmbientBattlefield;
    protected ref GM_TriggerManager m_pGM_TriggerMgr;

    protected bool m_bGM_Initialized = false;
    protected int m_iGM_PlayerCount = 0;
    protected float m_fGM_MissionTime = 0;
    protected ref array<string> m_aGM_RecentEvents;
    protected int m_iGM_TotalCommands = 0;
    protected bool m_bGM_WarmupQueued = false;

    // Player tracking for velocity/heading/trail computation
    protected ref map<int, vector> m_mGM_PlayerLastPositions;
    protected ref map<int, float> m_mGM_PlayerLastUpdateTimes;
    protected ref map<int, ref array<vector>> m_mGM_PlayerPositionTrails;

    override void OnWorldPostProcess(World world)
    {
        super.OnWorldPostProcess(world);

        if (!Replication.IsServer()) return;
        if (m_bGM_Initialized) return;

        // Enable EOnFrame dispatch so mission time is tracked
        SetEventMask(this, EntityEvent.FRAME);

        // Check if GM_GodModeComponent is already attached via Workbench
        GM_GodModeComponent existing = GM_GodModeComponent.Cast(FindComponent(GM_GodModeComponent));
        if (existing)
        {
            Print("[GodMode:Hook] GM_GodModeComponent found on entity, skipping hook bootstrap");
            m_bGM_Initialized = true;
            return;
        }

        Print("[GodMode:Hook] =============================================");
        Print("[GodMode:Hook] AI War -- Conflict Hook Bootstrap");
        Print("[GodMode:Hook] Injecting GodMode into base Conflict scenario");
        Print("[GodMode:Hook] =============================================");

        m_aGM_RecentEvents = {};
        m_mGM_PlayerLastPositions = new map<int, vector>();
        m_mGM_PlayerLastUpdateTimes = new map<int, float>();
        m_mGM_PlayerPositionTrails = new map<int, ref array<vector>>();

        // Init sub-managers
        m_pGM_WeatherCtrl = new GM_WeatherController();
        m_pGM_VehicleMgr = new GM_VehicleManager();
        m_pGM_SquadMgr = new GM_SquadManager();
        m_pGM_CleanupSys = new GM_CleanupSystem();
        m_pGM_SoundCtrl = new GM_SoundController();
        m_pGM_ParticleCtrl = new GM_ParticleController();
        m_pGM_NarrativeMgr = new GM_NarrativeManager();
        m_pGM_TriggerMgr = new GM_TriggerManager();
        m_pGM_MissionEngine = new GM_MissionEngine();
        m_pGM_AmbientBattlefield = new GM_AmbientBattlefield();
        m_pGM_AmbientBattlefield.Init(m_pGM_SoundCtrl, m_pGM_ParticleCtrl);

        // Initialize managers that have Init() methods
        m_pGM_SquadMgr.Init();
        m_pGM_VehicleMgr.Init();

        // NOTE: SetOwner() calls are skipped for GM_SquadManager, GM_MissionEngine,
        // and GM_TriggerManager because they expect GM_GodModeComponent as the
        // parameter type, and this hook is SCR_GameModeCampaign. All sub-manager
        // usages of m_pOwner are null-guarded, so they function without it —
        // they just cannot call back to LogEvent() or GetMissionTime().

        // Apply limits
        m_pGM_SquadMgr.SetMaxSquads(30);
        m_pGM_VehicleMgr.SetMaxVehicles(20);

        if (m_pGM_WeatherCtrl.HasManager())
            Print("[GodMode:Hook] Weather/time controls available");
        else
            Print("[GodMode:Hook] Weather/time controls unavailable", LogLevel.WARNING);

        // Init REST client
        m_pGM_RestClient = new GM_RestClient("http://localhost:8080");
        if (m_pGM_RestClient.Init())
        {
            m_pGM_RestClient.GetCallback().m_OnResponse.Insert(OnGM_LLMResponse);
            m_pGM_RestClient.GetCallback().m_OnError.Insert(OnGM_LLMError);
            Print("[GodMode:Hook] REST client connected -> " + m_pGM_RestClient.GetBaseURL());
        }
        else
        {
            Print("[GodMode:Hook] REST client FAILED — running offline", LogLevel.WARNING);
        }

        m_bGM_Initialized = true;

        // Start the SITREP loop (every 30 seconds)
        GetGame().GetCallqueue().CallLater(GM_SendSITREP, 30000, true);

        Print("[GodMode:Hook] Bootstrap complete. Awaiting players.");
        Print("[GodMode:Hook] =============================================");
    }

    // ── SITREP Loop ──

    void GM_SendSITREP()
    {
        if (!m_pGM_RestClient || !m_pGM_RestClient.IsConnected()) return;

        string json = GM_BuildGameStateJSON();
        m_pGM_RestClient.SendSITREP(json);
    }

    protected string GM_BuildGameStateJSON()
    {
        // Collect player data — only include players with a valid spawned entity
        // and a non-origin position, so the middleware never sees [0,0,0] ghosts.
        string playersJSON = "[]";
        array<int> playerIds = {};
        GetGame().GetPlayerManager().GetPlayers(playerIds);

        // Sync player count from the authoritative player list each tick
        m_iGM_PlayerCount = playerIds.Count();

        if (playerIds.Count() > 0)
        {
            string entries = "";
            int validCount = 0;
            for (int i = 0; i < playerIds.Count(); i++)
            {
                int pid = playerIds[i];
                string name = GetGame().GetPlayerManager().GetPlayerName(pid);

                // GetPlayerControlledEntity returns null when the player is in
                // the respawn/role-selection screen or hasn't spawned yet.
                IEntity controlled = GetGame().GetPlayerManager().GetPlayerControlledEntity(pid);
                if (!controlled)
                {
                    Print("[GodMode:Hook] Player " + pid + " (" + name + ") has no controlled entity — skipping");
                    continue;
                }

                vector pos = controlled.GetOrigin();

                // Guard against (0,0,0) which means entity exists but position
                // hasn't been replicated yet (e.g. first frame after spawn)
                if (pos[0] == 0 && pos[1] == 0 && pos[2] == 0)
                {
                    Print("[GodMode:Hook] Player " + pid + " (" + name + ") at origin — skipping");
                    continue;
                }

                // Compute heading from entity yaw
                float heading = 0;
                vector angles = controlled.GetAngles();
                if (angles)
                    heading = angles[1]; // Enfusion: [pitch, yaw, roll] — yaw is index 1

                // Compute velocity from position delta
                float vx = 0, vy = 0, vz = 0;
                float now = GetGame().GetTickTime();
                if (m_mGM_PlayerLastPositions && m_mGM_PlayerLastPositions.Contains(pid))
                {
                    vector lastPos = m_mGM_PlayerLastPositions[pid];
                    float lastTime = m_mGM_PlayerLastUpdateTimes[pid];
                    float dt = now - lastTime;
                    if (dt > 0.01)
                    {
                        vx = (pos[0] - lastPos[0]) / dt;
                        vy = (pos[1] - lastPos[1]) / dt;
                        vz = (pos[2] - lastPos[2]) / dt;
                    }
                }

                // Update tracking maps
                if (m_mGM_PlayerLastPositions)
                {
                    m_mGM_PlayerLastPositions[pid] = pos;
                    m_mGM_PlayerLastUpdateTimes[pid] = now;
                }

                // Build position trail (last 5 positions)
                string trailJSON = "[]";
                if (m_mGM_PlayerPositionTrails)
                {
                    if (!m_mGM_PlayerPositionTrails.Contains(pid))
                        m_mGM_PlayerPositionTrails[pid] = new array<vector>();

                    array<vector> trail = m_mGM_PlayerPositionTrails[pid];
                    trail.Insert(pos);
                    if (trail.Count() > 5)
                        trail.Remove(0);

                    string trailEntries = "";
                    for (int t = 0; t < trail.Count(); t++)
                    {
                        if (t > 0) trailEntries += ",";
                        trailEntries += string.Format("[%1,%2,%3]", trail[t][0], trail[t][1], trail[t][2]);
                    }
                    trailJSON = "[" + trailEntries + "]";
                }

                if (validCount > 0) entries += ",";
                entries += string.Format(
                    "{\"id\":%1,\"name\":\"%2\",\"position\":[%3,%4,%5],\"heading\":%6,\"velocity\":[%7,%8,%9],\"position_trail\":%10}",
                    pid, name, pos[0], pos[1], pos[2], heading, vx, vy, vz, trailJSON
                );
                validCount++;
            }

            if (validCount > 0)
                playersJSON = "[" + entries + "]";
        }

        // Events
        string eventsJSON = "[]";
        if (m_aGM_RecentEvents && m_aGM_RecentEvents.Count() > 0)
        {
            eventsJSON = "[";
            for (int e = 0; e < m_aGM_RecentEvents.Count(); e++)
            {
                if (e > 0) eventsJSON += ",";
                eventsJSON += "\"" + m_aGM_RecentEvents[e] + "\"";
            }
            eventsJSON += "]";
            m_aGM_RecentEvents.Clear();
        }

        // Weather/time
        float timeOfDay = 12.0;
        string weather = "clear";
        if (m_pGM_WeatherCtrl && m_pGM_WeatherCtrl.HasManager())
        {
            timeOfDay = m_pGM_WeatherCtrl.GetTimeOfDay();
            weather = m_pGM_WeatherCtrl.GetCurrentWeatherID();
        }

        string json = string.Format(
            "{\"players\":%1,\"player_count\":%2,\"mission_time_seconds\":%3,\"time_of_day\":%4,\"weather\":\"%5\",\"events\":%6,\"squads_active\":%7,\"vehicles_active\":%8,\"commands_executed\":%9}",
            playersJSON,
            m_iGM_PlayerCount,
            m_fGM_MissionTime,
            timeOfDay,
            weather,
            eventsJSON,
            m_pGM_SquadMgr.GetSquadCount(),
            m_pGM_VehicleMgr.GetVehicleCount(),
            m_iGM_TotalCommands
        );

        return json;
    }

    // ── LLM Response Handling ──

    void OnGM_LLMResponse(string data)
    {
        if (!m_pGM_RestClient) return;
        m_pGM_RestClient.OnSuccess();

        if (data == "" || data == "{}")
        {
            Print("[GodMode:Hook] Empty LLM response — no orders this cycle");
            return;
        }

        GM_CommandBatch batch = new GM_CommandBatch();
        batch.ExpandFromRAW(data);

        // Global commands
        if (batch.weather_command != "" && batch.weather_command != "none" && m_pGM_WeatherCtrl)
            m_pGM_WeatherCtrl.SetWeather(batch.weather_command);

        if (batch.time_override >= 0 && m_pGM_WeatherCtrl)
            m_pGM_WeatherCtrl.SetTimeOfDay(batch.time_override);

        if (batch.narrative_broadcast != "" && m_pGM_NarrativeMgr)
            m_pGM_NarrativeMgr.BroadcastMessage(batch.narrative_broadcast);

        if (!batch.commands || batch.commands.Count() == 0)
        {
            Print("[GodMode:Hook] No commands this cycle (LLM processing in background)");
            return;
        }

        int executed = 0;
        int count = batch.commands.Count();
        foreach (GM_Command cmd : batch.commands)
        {
            if (!cmd.IsValid())
                continue;
            GM_ExecuteCommand(cmd);
            executed++;
        }
        m_iGM_TotalCommands += executed;
        Print("[GodMode:Hook] Executed " + executed + "/" + count +
              " commands (total: " + m_iGM_TotalCommands + ")");
    }

    // ═══════════════════════════════════════════════
    // Command Dispatcher — mirrors GM_GodModeComponent.ExecuteCommand (all 72 types)
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteCommand(GM_Command cmd)
    {
        Print("[GodMode:Hook] CMD: " + cmd.command_type + " | unit=" + cmd.unit_id);

        switch (cmd.command_type)
        {
            // ── Movement & Combat ──
            case GM_CommandTypes.MOVE:
                m_pGM_SquadMgr.OrderMove(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.ATTACK:
                m_pGM_SquadMgr.OrderAttack(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.DEFEND:
                m_pGM_SquadMgr.OrderDefend(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.PATROL:
                m_pGM_SquadMgr.OrderPatrol(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.SET_AMBUSH:
                m_pGM_SquadMgr.OrderAmbush(cmd.unit_id, cmd.GetTargetVector(), cmd.parameters); break;
            case GM_CommandTypes.SEEK_AND_DESTROY:
                m_pGM_SquadMgr.OrderSeekDestroy(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.RETREAT:
                m_pGM_SquadMgr.OrderRetreat(cmd.unit_id, cmd.GetTargetVector()); break;

            // ── Advanced Tactical (Squad Manager) ──
            case GM_CommandTypes.SUPPRESS:
                m_pGM_SquadMgr.OrderSuppress(cmd.unit_id, cmd.GetTargetVector(), cmd.parameters); break;
            case GM_CommandTypes.OVERWATCH:
                m_pGM_SquadMgr.OrderOverwatch(cmd.unit_id, cmd.GetTargetVector(), cmd.parameters); break;
            case GM_CommandTypes.SILENT_MOVE:
                m_pGM_SquadMgr.OrderSilentMove(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.GARRISON:
                m_pGM_SquadMgr.OrderGarrison(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.ESTABLISH_OP:
                m_pGM_SquadMgr.OrderEstablishOP(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.FIGHTING_WITHDRAWAL:
                m_pGM_SquadMgr.OrderFightingWithdrawal(cmd.unit_id, cmd.parameters); break;
            case GM_CommandTypes.PATROL_CLOVERLEAF:
                m_pGM_SquadMgr.OrderPatrolCloverleaf(cmd.unit_id, cmd.GetTargetVector(), 200); break;
            case GM_CommandTypes.PATROL_ZIGZAG:
            {
                SCR_AIGroup zigGroup = m_pGM_SquadMgr.GetGroupPublic(cmd.unit_id);
                vector zigStart = cmd.GetTargetVector();
                if (zigGroup)
                    zigStart = zigGroup.GetOrigin();
                m_pGM_SquadMgr.OrderPatrolZigzag(cmd.unit_id, zigStart, cmd.GetTargetVector(), 80);
                break;
            }

            // ── Compound Tactical ──
            case GM_CommandTypes.FLANK_LEFT:
                GM_ExecuteFlankManeuver(cmd, -1); break;
            case GM_CommandTypes.FLANK_RIGHT:
                GM_ExecuteFlankManeuver(cmd, 1); break;
            case GM_CommandTypes.BREACH:
                m_pGM_SquadMgr.OrderAttack(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.SET_KEYHOLE:
                m_pGM_SquadMgr.OrderDefend(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.BOUNDING_OVERWATCH:
                m_pGM_SquadMgr.OrderMove(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.COORDINATED_OP:
                GM_ExecuteCoordinatedOp(cmd); break;

            // ── Vehicle Tactical ──
            case GM_CommandTypes.HULL_DOWN:
                m_pGM_VehicleMgr.OrderVehicleDefend(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.OVERWATCH_POSITION:
                m_pGM_VehicleMgr.OrderVehicleDefend(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.DISMOUNT_ASSAULT:
                m_pGM_SquadMgr.OrderAttack(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.CAS_RUN:
                GM_ExecuteCASRun(cmd); break;
            case GM_CommandTypes.TROOP_INSERT:
                GM_ExecuteTroopInsert(cmd); break;
            case GM_CommandTypes.CONVOY_FORM:
                GM_ExecuteConvoyForm(cmd); break;
            case GM_CommandTypes.VEHICLE_SCREEN:
                GM_ExecuteVehicleScreen(cmd); break;
            case GM_CommandTypes.AIR_PATROL:
                Print("[GodMode:Hook] Air patrol requested — future implementation (logged only)"); break;

            // ── Spawning ──
            case GM_CommandTypes.SPAWN_INFANTRY:
                m_pGM_SquadMgr.SpawnGroup(cmd.parameters, cmd.GetTargetVector()); break;
            case GM_CommandTypes.SPAWN_VEHICLE:
                m_pGM_VehicleMgr.SpawnCrewedVehicle(cmd.parameters, cmd.GetTargetVector()); break;
            case GM_CommandTypes.SPAWN_REINFORCEMENTS:
                m_pGM_SquadMgr.SpawnReinforcements(cmd.parameters, cmd.GetTargetVector()); break;

            // ── Environment ──
            case GM_CommandTypes.SET_WEATHER:
                if (m_pGM_WeatherCtrl) m_pGM_WeatherCtrl.SetWeather(cmd.parameters); break;
            case GM_CommandTypes.SET_TIME:
            {
                float t = cmd.parameters.ToFloat();
                if (t >= 0 && t <= 24 && m_pGM_WeatherCtrl)
                    m_pGM_WeatherCtrl.SetTimeOfDay(t);
                break;
            }
            case GM_CommandTypes.SET_WIND:
                if (m_pGM_WeatherCtrl) m_pGM_WeatherCtrl.SetWind(cmd.parameters); break;
            case GM_CommandTypes.THUNDER_NEAR:
                if (m_pGM_SoundCtrl) m_pGM_SoundCtrl.PlayThunderNear(cmd.GetTargetVector()); break;
            case GM_CommandTypes.PLAY_SOUND:
                if (m_pGM_SoundCtrl) m_pGM_SoundCtrl.PlaySoundAt(cmd.parameters, cmd.GetTargetVector()); break;

            // ── Advanced Environment ──
            case GM_CommandTypes.SET_WEATHER_PROGRESSION:
                GM_ExecuteWeatherProgression(cmd); break;
            case GM_CommandTypes.SET_TACTICAL_LIGHTING:
                if (m_pGM_WeatherCtrl) m_pGM_WeatherCtrl.SetTacticalLighting(cmd.parameters, 60); break;
            case GM_CommandTypes.SET_AMBIENT_INTENSITY:
            {
                int amb = cmd.parameters.ToInt();
                if (m_pGM_AmbientBattlefield && amb >= 0 && amb <= 3)
                    m_pGM_AmbientBattlefield.SetIntensity(amb);
                break;
            }
            case GM_CommandTypes.ARTILLERY_BARRAGE:
                GM_ExecuteArtilleryBarrage(cmd); break;
            case GM_CommandTypes.BUILDING_FIRE:
                GM_ExecuteBuildingFire(cmd); break;
            case GM_CommandTypes.DISTANT_COMBAT:
                GM_ExecuteDistantCombat(cmd); break;
            case GM_CommandTypes.ENVIRONMENT_MOOD:
                if (m_pGM_WeatherCtrl) m_pGM_WeatherCtrl.SetEnvironmentMood(cmd.parameters); break;

            // ── Effects ──
            case GM_CommandTypes.EXPLOSION:
                if (m_pGM_ParticleCtrl) m_pGM_ParticleCtrl.SpawnExplosion(cmd.GetTargetVector(), cmd.parameters); break;
            case GM_CommandTypes.SMOKE:
                if (m_pGM_ParticleCtrl) m_pGM_ParticleCtrl.SpawnSmoke(cmd.GetTargetVector(), cmd.parameters); break;
            case GM_CommandTypes.FLARE:
                if (m_pGM_ParticleCtrl) m_pGM_ParticleCtrl.SpawnFlare(cmd.GetTargetVector()); break;

            // ── Cleanup ──
            case GM_CommandTypes.CLEANUP_AREA:
            {
                float r = cmd.parameters.ToFloat();
                if (r <= 0) r = 100;
                m_pGM_CleanupSys.CleanupRadius(cmd.GetTargetVector(), r);
                break;
            }
            case GM_CommandTypes.CLEANUP_DEAD:
                m_pGM_CleanupSys.CleanupAllDead(); break;
            case GM_CommandTypes.DESPAWN_UNIT:
                m_pGM_SquadMgr.DespawnGroup(cmd.unit_id); break;

            // ── Narrative ──
            case GM_CommandTypes.RADIO_MESSAGE:
                if (m_pGM_NarrativeMgr) m_pGM_NarrativeMgr.SendRadioMessage(cmd.narrative, cmd.parameters); break;
            case GM_CommandTypes.OBJECTIVE_CREATE:
                if (m_pGM_NarrativeMgr) m_pGM_NarrativeMgr.CreateObjective(cmd.parameters, cmd.narrative, cmd.GetTargetVector()); break;
            case GM_CommandTypes.OBJECTIVE_COMPLETE:
            {
                if (m_pGM_NarrativeMgr) m_pGM_NarrativeMgr.CompleteObjective(cmd.parameters);
                if (m_pGM_MissionEngine) m_pGM_MissionEngine.RecordObjectiveComplete(cmd.parameters);
                break;
            }
            case GM_CommandTypes.OBJECTIVE_FAIL:
            {
                if (m_pGM_NarrativeMgr) m_pGM_NarrativeMgr.FailObjective(cmd.parameters);
                if (m_pGM_MissionEngine) m_pGM_MissionEngine.RecordObjectiveFailed(cmd.parameters);
                break;
            }

            // ── Narrative Advanced ──
            case GM_CommandTypes.RADIO_COMMS:
                if (m_pGM_NarrativeMgr) m_pGM_NarrativeMgr.ShowNotification(cmd.parameters); break;
            case GM_CommandTypes.INTEL_FEED:
                if (m_pGM_NarrativeMgr) m_pGM_NarrativeMgr.ShowNotification(cmd.parameters); break;
            case GM_CommandTypes.FACTION_COMMS:
                if (m_pGM_NarrativeMgr) m_pGM_NarrativeMgr.ShowNotification(cmd.parameters); break;
            case GM_CommandTypes.INTERCEPTED_COMMS:
                if (m_pGM_NarrativeMgr) m_pGM_NarrativeMgr.ShowNotification(cmd.parameters); break;
            case GM_CommandTypes.PLAYER_ACK:
                if (m_pGM_NarrativeMgr) m_pGM_NarrativeMgr.ShowNotification(cmd.parameters); break;
            case GM_CommandTypes.PACING_DIRECTIVE:
                Print("[GodMode:Hook] Pacing directive (internal LLM): " + cmd.parameters); break;

            // ── Fire Support ──
            case GM_CommandTypes.MORTAR_STRIKE:
                GM_ExecuteMortarStrike(cmd); break;
            case GM_CommandTypes.AIR_SUPPORT:
                GM_ExecuteAirSupport(cmd); break;

            // ── Logistics ──
            case GM_CommandTypes.SUPPLY_DROP:
                GM_ExecuteSupplyDrop(cmd); break;
            case GM_CommandTypes.SPAWN_CONVOY:
                GM_ExecuteSpawnConvoy(cmd); break;

            // ── Fortification ──
            case GM_CommandTypes.ESTABLISH_CHECKPOINT:
                GM_ExecuteEstablishCheckpoint(cmd); break;
            case GM_CommandTypes.LAY_MINES:
                GM_ExecuteLayMines(cmd); break;
            case GM_CommandTypes.SET_ROADBLOCK:
                GM_ExecuteSetRoadblock(cmd); break;

            // ── Markers ──
            case GM_CommandTypes.SPAWN_MARKER:
                GM_ExecuteSpawnMarker(cmd); break;

            // ── Meta / Mission Control ──
            case GM_CommandTypes.SET_ALERT_LEVEL:
                if (m_pGM_MissionEngine) m_pGM_MissionEngine.SetAlertLevel(cmd.parameters); break;
            case GM_CommandTypes.ORDER_FORMATION:
                Print("[GodMode:Hook] Formation order: " + cmd.unit_id + " -> " + cmd.formation);
                break;
            case GM_CommandTypes.START_SCENARIO:
                GM_ExecuteStartScenario(cmd); break;
            case GM_CommandTypes.END_SCENARIO:
                if (m_pGM_MissionEngine) m_pGM_MissionEngine.EndScenario(cmd.parameters); break;

            default:
                Print("[GodMode:Hook] Unhandled command: " + cmd.command_type, LogLevel.WARNING);
                break;
        }

        // Broadcast narrative flavor text if present (and not already a narrative command)
        if (cmd.narrative != "" &&
            cmd.command_type != GM_CommandTypes.RADIO_MESSAGE &&
            cmd.command_type != GM_CommandTypes.OBJECTIVE_CREATE)
        {
            if (m_pGM_NarrativeMgr)
                m_pGM_NarrativeMgr.BroadcastMessage(cmd.narrative);
        }
    }

    void OnGM_LLMError(int errorCode)
    {
        if (!m_pGM_RestClient) return;
        m_pGM_RestClient.OnError();
        Print("[GodMode:Hook] LLM error: " + errorCode, LogLevel.WARNING);
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Flank Maneuver
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteFlankManeuver(GM_Command cmd, int side)
    {
        SCR_AIGroup group = m_pGM_SquadMgr.GetGroupPublic(cmd.unit_id);
        if (!group)
        {
            Print("[GodMode:Hook] Flank: squad " + cmd.unit_id + " not found", LogLevel.WARNING);
            return;
        }

        vector groupPos = group.GetOrigin();
        vector target = cmd.GetTargetVector();
        vector toTarget = target - groupPos;
        float dist = toTarget.Length();
        if (dist < 1) dist = 1;

        vector forward = toTarget / dist;
        vector perp = Vector(forward[2] * side, 0, -forward[0] * side);

        float lateralOffset = 120.0;
        float advanceRatio = 0.6;

        vector flankWaypoint = Vector(
            groupPos[0] + forward[0] * dist * advanceRatio + perp[0] * lateralOffset,
            0,
            groupPos[2] + forward[2] * dist * advanceRatio + perp[2] * lateralOffset
        );
        flankWaypoint[1] = GetGame().GetWorld().GetSurfaceY(flankWaypoint[0], flankWaypoint[2]);

        group.CompleteAllWaypoints();
        m_pGM_SquadMgr.OrderMove(cmd.unit_id, flankWaypoint);

        string attackSquadId = cmd.unit_id;
        vector attackTarget = target;
        GetGame().GetCallqueue().CallLater(GM_DelayedFlankAttack, 3000, false, attackSquadId, attackTarget);

        string sideStr = "RIGHT";
        if (side < 0) sideStr = "LEFT";
        Print("[GodMode:Hook] Flank " + sideStr + ": " + cmd.unit_id +
              " via " + flankWaypoint.ToString(false) +
              " -> " + target.ToString(false));
        GM_LogEvent("flank_" + sideStr + ":" + cmd.unit_id);
    }

    protected void GM_DelayedFlankAttack(string squadId, vector target)
    {
        m_pGM_SquadMgr.OrderAttack(squadId, target);
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Coordinated Operation
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteCoordinatedOp(GM_Command cmd)
    {
        Print("[GodMode:Hook] Coordinated operation: unit=" + cmd.unit_id +
              " | params=" + cmd.parameters);

        if (cmd.unit_id != "" && cmd.unit_id != "new")
            m_pGM_SquadMgr.OrderMove(cmd.unit_id, cmd.GetTargetVector());

        GM_LogEvent("coordinated_op:" + cmd.unit_id);
    }

    // ═══════════════════════════════════════════════
    // Command Helper: CAS Run (Close Air Support)
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteCASRun(GM_Command cmd)
    {
        vector target = cmd.GetTargetVector();
        string strikeType = "gun_run";
        float bearing = 270;
        int passes = 1;

        if (cmd.parameters.Contains("bomb"))
            strikeType = "bomb_run";
        if (cmd.parameters.Contains("rocket"))
            strikeType = "rocket_run";

        if (m_pGM_AmbientBattlefield)
            m_pGM_AmbientBattlefield.ExecuteAirStrike(target, strikeType, bearing, passes);

        Print("[GodMode:Hook] CAS run: " + strikeType + " at " + target.ToString(false));
        GM_LogEvent("cas_run:" + strikeType);
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Troop Insert
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteTroopInsert(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();
        string squadType = "ussr_rifle_squad";
        if (cmd.parameters != "" && cmd.parameters != "{}")
            squadType = cmd.parameters;

        if (m_pGM_SoundCtrl)
        {
            vector heliPos = position;
            heliPos[1] = heliPos[1] + 50;
            m_pGM_SoundCtrl.PlaySoundAt("SOUND_HELICOPTER_DISTANT", heliPos);
        }

        string sid = m_pGM_SquadMgr.SpawnGroup(squadType, position);
        if (sid != "")
            m_pGM_SquadMgr.OrderDefend(sid, position);

        Print("[GodMode:Hook] Troop insert: " + squadType + " at " + position.ToString(false));
        GM_LogEvent("troop_insert:" + squadType);
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Convoy Form
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteConvoyForm(GM_Command cmd)
    {
        vector startPos = cmd.GetTargetVector();
        ref array<string> vehicleTypes = {};

        if (cmd.parameters != "" && cmd.parameters.Contains(","))
        {
            cmd.parameters.Split(",", vehicleTypes, false);
        }
        else
        {
            vehicleTypes = {"uaz469_mg", "ural_truck", "ural_truck", "uaz469_mg"};
        }

        string convoyId = "convoy_" + m_iGM_TotalCommands;
        float spacing = 30;
        vector destination = startPos;
        m_pGM_VehicleMgr.SpawnConvoy(convoyId, vehicleTypes, startPos, destination, spacing);

        Print("[GodMode:Hook] Convoy formed: " + vehicleTypes.Count() + " vehicles at " + startPos.ToString(false));
        GM_LogEvent("convoy_form:" + vehicleTypes.Count());
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Vehicle Screen
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteVehicleScreen(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();
        m_pGM_VehicleMgr.OrderVehicleDefend(cmd.unit_id, position);

        Print("[GodMode:Hook] Vehicle screen: " + cmd.unit_id + " defending at " + position.ToString(false));
        GM_LogEvent("vehicle_screen:" + cmd.unit_id);
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Weather Progression
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteWeatherProgression(GM_Command cmd)
    {
        string targetWeather = "overcast";
        float totalMinutes = 30;
        bool thenClear = false;

        if (cmd.parameters != "" && cmd.parameters.Substring(0, 1) != "{")
        {
            ref array<string> parts = {};
            cmd.parameters.Split(",", parts, false);
            if (parts.Count() >= 1)
                targetWeather = parts[0].Trim();
            if (parts.Count() >= 2 && parts[1].Trim().ToFloat() > 0)
                totalMinutes = parts[1].Trim().ToFloat();
            if (parts.Count() >= 3)
                thenClear = (parts[2].Trim() == "true" || parts[2].Trim() == "1");
        }

        if (m_pGM_WeatherCtrl)
            m_pGM_WeatherCtrl.StartWeatherProgression(targetWeather, totalMinutes, thenClear);

        Print("[GodMode:Hook] Weather progression: " + targetWeather + " over " + totalMinutes + "min (clear after: " + thenClear + ")");
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Artillery Barrage
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteArtilleryBarrage(GM_Command cmd)
    {
        vector center = cmd.GetTargetVector();
        float radius = 100;
        int rounds = 12;
        float duration = 30;
        string caliber = "standard";

        string paramStr = cmd.parameters;
        if (paramStr != "" && paramStr.Substring(0, 1) != "{")
        {
            ref array<string> parts = {};
            paramStr.Split(",", parts, false);
            if (parts.Count() >= 1 && parts[0].Trim().ToFloat() > 0)
                radius = parts[0].Trim().ToFloat();
            if (parts.Count() >= 2 && parts[1].Trim().ToInt() > 0)
                rounds = parts[1].Trim().ToInt();
            if (parts.Count() >= 3 && parts[2].Trim().ToFloat() > 0)
                duration = parts[2].Trim().ToFloat();
            if (parts.Count() >= 4)
                caliber = parts[3].Trim();
        }

        if (radius <= 0) radius = 100;
        if (rounds <= 0) rounds = 12;
        if (duration <= 0) duration = 30;

        if (m_pGM_AmbientBattlefield)
            m_pGM_AmbientBattlefield.ExecuteArtilleryBarrage(center, radius, rounds, duration, caliber);

        Print("[GodMode:Hook] Artillery barrage: " + rounds + " rounds of " + caliber +
              " at radius " + radius + "m for " + duration + "s");
        GM_LogEvent("artillery_barrage:" + center.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Building Fire
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteBuildingFire(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();
        string intensity = "medium";
        float durationMinutes = 10;

        if (cmd.parameters != "" && cmd.parameters.Substring(0, 1) != "{")
        {
            ref array<string> parts = {};
            cmd.parameters.Split(",", parts, false);
            if (parts.Count() >= 1)
                intensity = parts[0].Trim();
            if (parts.Count() >= 2 && parts[1].Trim().ToFloat() > 0)
                durationMinutes = parts[1].Trim().ToFloat();
        }

        if (durationMinutes <= 0) durationMinutes = 10;

        if (m_pGM_AmbientBattlefield)
            m_pGM_AmbientBattlefield.StartBuildingFire(position, intensity, durationMinutes);

        Print("[GodMode:Hook] Building fire: " + intensity + " at " + position.ToString(false) + " for " + durationMinutes + "min");
        GM_LogEvent("building_fire:" + position.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Distant Combat
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteDistantCombat(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();
        string combatType = "firefight";
        float durationSeconds = 60;
        string intensity = "moderate";

        ref array<string> parts = {};
        cmd.parameters.Split(",", parts, false);
        if (parts.Count() >= 1)
            combatType = parts[0].Trim();
        if (parts.Count() >= 2)
            durationSeconds = parts[1].Trim().ToFloat();
        if (parts.Count() >= 3)
            intensity = parts[2].Trim();

        if (durationSeconds <= 0) durationSeconds = 60;

        if (m_pGM_AmbientBattlefield)
            m_pGM_AmbientBattlefield.StartDistantCombat(position, combatType, durationSeconds, intensity);

        Print("[GodMode:Hook] Distant combat: " + combatType + " (" + intensity + ") at " +
              position.ToString(false) + " for " + durationSeconds + "s");
        GM_LogEvent("distant_combat:" + combatType);
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Mortar Strike
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteMortarStrike(GM_Command cmd)
    {
        vector center = cmd.GetTargetVector();
        int rounds = 6;
        float spreadRadius = 40;
        int delayMs = 1500;
        string strikeType = "he";

        if (cmd.parameters.Contains("smoke"))
            strikeType = "smoke";

        Print("[GodMode:Hook] Mortar strike: " + rounds + " rounds, " + spreadRadius + "m spread at " + center.ToString(false));

        for (int i = 0; i < rounds; i++)
        {
            float angle = Math.RandomFloat(0, Math.PI2);
            float dist = Math.RandomFloat(0, spreadRadius);
            vector impactPos = center;
            impactPos[0] = impactPos[0] + Math.Cos(angle) * dist;
            impactPos[2] = impactPos[2] + Math.Sin(angle) * dist;

            int totalDelay = i * delayMs;

            if (strikeType == "smoke")
                GetGame().GetCallqueue().CallLater(GM_MortarSmokeImpact, totalDelay, false, impactPos);
            else
                GetGame().GetCallqueue().CallLater(GM_MortarHEImpact, totalDelay, false, impactPos);
        }

        if (m_pGM_MissionEngine)
            m_pGM_MissionEngine.RecordOpforSpawn();
    }

    protected void GM_MortarHEImpact(vector position)
    {
        if (m_pGM_ParticleCtrl) m_pGM_ParticleCtrl.SpawnExplosion(position, "large");
        if (m_pGM_SoundCtrl) m_pGM_SoundCtrl.PlayThunderNear(position);
    }

    protected void GM_MortarSmokeImpact(vector position)
    {
        if (m_pGM_ParticleCtrl) m_pGM_ParticleCtrl.SpawnSmoke(position, "white");
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Air Support
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteAirSupport(GM_Command cmd)
    {
        vector center = cmd.GetTargetVector();
        float dirDeg = 270;
        float runLength = 200;

        float dirRad = dirDeg * Math.DEG2RAD;
        vector start = center;
        start[0] = start[0] - Math.Cos(dirRad) * runLength * 0.5;
        start[2] = start[2] - Math.Sin(dirRad) * runLength * 0.5;

        int impacts = 10;
        float stepX = Math.Cos(dirRad) * runLength / impacts;
        float stepZ = Math.Sin(dirRad) * runLength / impacts;

        Print("[GodMode:Hook] Air support: gun run along " + runLength + "m at " + center.ToString(false));

        for (int i = 0; i < impacts; i++)
        {
            vector impactPos = start;
            impactPos[0] = impactPos[0] + stepX * i + Math.RandomFloat(-10, 10);
            impactPos[2] = impactPos[2] + stepZ * i + Math.RandomFloat(-10, 10);

            int delay = i * 200;
            GetGame().GetCallqueue().CallLater(GM_MortarHEImpact, delay, false, impactPos);
        }
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Supply Drop
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteSupplyDrop(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();
        if (m_pGM_ParticleCtrl)
        {
            m_pGM_ParticleCtrl.SpawnSmoke(position, "green");
            m_pGM_ParticleCtrl.SpawnFlare(position);
        }
        Print("[GodMode:Hook] Supply drop marked at " + position.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Spawn Convoy
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteSpawnConvoy(GM_Command cmd)
    {
        vector startPos = cmd.GetTargetVector();
        ref array<string> vehicleTypes = {"uaz469_mg", "ural_truck", "ural_truck", "uaz469_mg"};
        float spacing = 30;

        for (int i = 0; i < vehicleTypes.Count(); i++)
        {
            vector vehPos = startPos;
            vehPos[2] = vehPos[2] - (spacing * i);
            m_pGM_VehicleMgr.SpawnCrewedVehicle(vehicleTypes[i], vehPos, "ussr");
        }

        Print("[GodMode:Hook] Convoy spawned: " + vehicleTypes.Count() + " vehicles at " + startPos.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Establish Checkpoint
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteEstablishCheckpoint(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();
        string size = "medium";

        if (cmd.parameters.Contains("small"))
            size = "small";
        else if (cmd.parameters.Contains("large"))
            size = "large";

        if (size == "small")
        {
            m_pGM_SquadMgr.SpawnGroup("ussr_fire_team", position);
        }
        else if (size == "medium")
        {
            string sid = m_pGM_SquadMgr.SpawnGroup("ussr_rifle_squad", position);
            if (sid != "")
                m_pGM_SquadMgr.OrderDefend(sid, position);
            m_pGM_VehicleMgr.SpawnCrewedVehicle("uaz469_mg", position, "ussr");
        }
        else
        {
            string sid1 = m_pGM_SquadMgr.SpawnGroup("ussr_rifle_squad", position);
            string sid2 = m_pGM_SquadMgr.SpawnGroup("ussr_mg_team",
                Vector(position[0] + 20, 0, position[2]));
            if (sid1 != "") m_pGM_SquadMgr.OrderDefend(sid1, position);
            if (sid2 != "") m_pGM_SquadMgr.OrderDefend(sid2, position);
            m_pGM_VehicleMgr.SpawnCrewedVehicle("btr70", position, "ussr");
        }

        Print("[GodMode:Hook] Checkpoint established (" + size + ") at " + position.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Lay Mines
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteLayMines(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();
        float radius = 30;
        int mineCount = 6;
        string mineType = "anti_personnel";

        if (cmd.parameters.Contains("sparse"))
            mineCount = 3;
        else if (cmd.parameters.Contains("dense"))
            mineCount = 12;

        if (cmd.parameters.Contains("anti_vehicle"))
            mineType = "anti_vehicle";

        if (m_pGM_MissionEngine)
        {
            string minefieldId = m_pGM_MissionEngine.CreateMinefield(position, radius, mineCount, mineType);
            Print("[GodMode:Hook] Minefield laid: " + minefieldId);
        }
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Set Roadblock
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteSetRoadblock(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();

        vector routeDir = Vector(1, 0, 0);
        if (m_mGM_PlayerPositionTrails && m_mGM_PlayerPositionTrails.Count() > 0)
            routeDir = GM_EstimateRouteDirection(position);
        vector lateral = Vector(-routeDir[2], 0, routeDir[0]);

        string centerObstacle = "ural_truck";
        string flankObstacle = "uaz469";
        string defenders = "ussr_fire_team";

        if (cmd.parameters.Contains("heavy") || cmd.parameters.Contains("armor"))
        {
            centerObstacle = "btr70";
            flankObstacle = "ural_truck";
            defenders = "ussr_rifle_squad";
        }

        m_pGM_VehicleMgr.SpawnVehicle(centerObstacle, position);
        m_pGM_VehicleMgr.SpawnVehicle(flankObstacle, GM_OffsetPosition(position, lateral, 9.0));
        m_pGM_VehicleMgr.SpawnVehicle(flankObstacle, GM_OffsetPosition(position, lateral, -9.0));

        if (cmd.parameters.Contains("deep") || cmd.parameters.Contains("checkpoint"))
        {
            m_pGM_VehicleMgr.SpawnVehicle("uaz469", GM_OffsetPosition(position, routeDir, 10.0));
            m_pGM_VehicleMgr.SpawnVehicle("uaz469", GM_OffsetPosition(position, routeDir, -10.0));
        }

        string squadId = m_pGM_SquadMgr.SpawnGroup(defenders, GM_OffsetPosition(position, routeDir, 20.0));
        if (squadId != "")
            m_pGM_SquadMgr.OrderDefend(squadId, position);

        if (cmd.parameters.Contains("mine") && m_pGM_MissionEngine)
            m_pGM_MissionEngine.CreateMinefield(position, 25, 4, "anti_vehicle");

        if (m_pGM_ParticleCtrl)
        {
            m_pGM_ParticleCtrl.SpawnSmoke(position, "white");
            m_pGM_ParticleCtrl.SpawnSmoke(GM_OffsetPosition(position, lateral, 6.0), "white");
        }

        Print("[GodMode:Hook] Roadblock set at " + position.ToString(false) +
              " using " + centerObstacle + " + " + flankObstacle + " obstacles");
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Spawn Marker
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteSpawnMarker(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();

        if (cmd.parameters.Contains("smoke_signal"))
        {
            if (m_pGM_ParticleCtrl) m_pGM_ParticleCtrl.SpawnSmoke(position, "green");
        }
        else if (cmd.parameters.Contains("strobe"))
        {
            if (m_pGM_ParticleCtrl) m_pGM_ParticleCtrl.SpawnFlare(position);
        }
        else
        {
            if (m_pGM_ParticleCtrl) m_pGM_ParticleCtrl.SpawnSmoke(position, "green");
        }

        Print("[GodMode:Hook] Marker placed at " + position.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Command Helper: Start Scenario
    // ═══════════════════════════════════════════════

    protected void GM_ExecuteStartScenario(GM_Command cmd)
    {
        string scenario = "custom";
        string archetype = "patrol_ambush";
        string curve = "standard_bell";
        float duration = 40;

        if (m_pGM_MissionEngine)
            m_pGM_MissionEngine.StartScenario(scenario, archetype, curve, duration);
    }

    // ═══════════════════════════════════════════════
    // Utility: Offset Position (position + direction * distance, snapped to terrain)
    // ═══════════════════════════════════════════════

    protected vector GM_OffsetPosition(vector origin, vector direction, float distance)
    {
        vector dir = direction;
        dir[1] = 0;

        float dirLength = dir.Length();
        if (dirLength <= 0.01)
            dir = Vector(1, 0, 0);
        else
            dir = dir / dirLength;

        vector result = origin;
        result[0] = result[0] + dir[0] * distance;
        result[2] = result[2] + dir[2] * distance;
        result[1] = GetGame().GetWorld().GetSurfaceY(result[0], result[2]);
        return result;
    }

    // ═══════════════════════════════════════════════
    // Utility: Estimate Route Direction from Player Trails
    // ═══════════════════════════════════════════════

    protected vector GM_EstimateRouteDirection(vector nearPosition)
    {
        vector bestDirection = Vector(1, 0, 0);
        float bestDistance = 999999.0;

        for (int i = 0; i < m_mGM_PlayerPositionTrails.Count(); i++)
        {
            ref array<vector> trail = m_mGM_PlayerPositionTrails.GetElement(i);
            if (!trail || trail.Count() < 2)
                continue;

            vector latest = trail[trail.Count() - 1];
            vector prev = trail[trail.Count() - 2];
            vector trailDir = latest - prev;
            trailDir[1] = 0;
            float trailLen = trailDir.Length();
            if (trailLen <= 0.1)
                continue;

            float dx = latest[0] - nearPosition[0];
            float dz = latest[2] - nearPosition[2];
            float dist = Math.Sqrt(dx * dx + dz * dz);
            if (dist < bestDistance)
            {
                bestDistance = dist;
                bestDirection = trailDir / trailLen;
            }
        }

        if (bestDirection.Length() <= 0.01)
            return Vector(1, 0, 0);

        return bestDirection;
    }

    // ── Player Events ──

    override void OnPlayerConnected(int playerId)
    {
        super.OnPlayerConnected(playerId);
        if (!m_bGM_Initialized) return;

        // m_iGM_PlayerCount is now synced from GetPlayers() in GM_BuildGameStateJSON
        GM_LogEvent("player_connected:" + playerId);
        Print("[GodMode:Hook] Player connected: " + playerId);

        if (!m_bGM_WarmupQueued && m_pGM_RestClient && m_pGM_RestClient.IsConnected())
        {
            m_bGM_WarmupQueued = true;
            GetGame().GetCallqueue().CallLater(GM_SendSITREP, 5000, false);
            Print("[GodMode:Hook] Warm-up SITREP queued in 5s");
        }
    }

    void GM_OnPlayerLeft(int playerId)
    {
        if (!m_bGM_Initialized) return;

        // m_iGM_PlayerCount is now synced from GetPlayers() in GM_BuildGameStateJSON
        GM_LogEvent("player_disconnected:" + playerId);

        // Clean up tracking maps for departed player
        if (m_mGM_PlayerLastPositions && m_mGM_PlayerLastPositions.Contains(playerId))
            m_mGM_PlayerLastPositions.Remove(playerId);
        if (m_mGM_PlayerLastUpdateTimes && m_mGM_PlayerLastUpdateTimes.Contains(playerId))
            m_mGM_PlayerLastUpdateTimes.Remove(playerId);
        if (m_mGM_PlayerPositionTrails && m_mGM_PlayerPositionTrails.Contains(playerId))
            m_mGM_PlayerPositionTrails.Remove(playerId);
    }

    // ── Frame Tick ──

    override void EOnFrame(IEntity owner, float timeSlice)
    {
        super.EOnFrame(owner, timeSlice);
        if (!m_bGM_Initialized) return;

        m_fGM_MissionTime += timeSlice;

        // Tick weather progression (transitions, tactical lighting, etc.)
        if (m_pGM_WeatherCtrl)
            m_pGM_WeatherCtrl.TickProgression(timeSlice);
    }

    // ── Utilities ──

    void GM_LogEvent(string eventStr)
    {
        if (!m_aGM_RecentEvents) return;
        m_aGM_RecentEvents.Insert(eventStr);
        if (m_aGM_RecentEvents.Count() > 50)
            m_aGM_RecentEvents.Remove(0);
    }
}
