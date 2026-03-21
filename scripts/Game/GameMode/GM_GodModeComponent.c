// GM_GodModeComponent.c — Central LLM God Mode Game Master Orchestrator
// Attach this component to your GameMode entity in the Workbench.
// It initializes all sub-managers, collects SITREP data, dispatches LLM commands,
// and handles the complete game loop.

[EntityEditorProps(category: "GameScripted/GodMode", description: "LLM God Mode Game Master — AI-driven dynamic mission controller")]
class GM_GodModeComponentClass : SCR_BaseGameModeComponentClass {}

class GM_GodModeComponent : SCR_BaseGameModeComponent
{
    // ═══════════════════════════════════════════════
    // Configuration (editable in Workbench)
    // ═══════════════════════════════════════════════

    [Attribute("30", UIWidgets.Slider, desc: "Seconds between LLM updates", params: "5 300 1")]
    protected float m_fUpdateInterval;

    [Attribute("http://localhost:8000", desc: "FastAPI middleware server URL")]
    protected string m_sServerURL;

    [Attribute("1", desc: "Enable LLM communication (0=offline mode)")]
    protected bool m_bLLMEnabled;

    [Attribute("30", desc: "Max AI squads allowed")]
    protected int m_iMaxSquads;

    [Attribute("20", desc: "Max vehicles allowed")]
    protected int m_iMaxVehicles;

    // ═══════════════════════════════════════════════
    // Sub-Managers
    // ═══════════════════════════════════════════════

    protected ref GM_RestClient m_RestClient;
    protected ref GM_WeatherController m_WeatherCtrl;
    protected ref GM_VehicleManager m_VehicleMgr;
    protected ref GM_SquadManager m_SquadMgr;
    protected ref GM_CleanupSystem m_CleanupSys;
    protected ref GM_SoundController m_SoundCtrl;
    protected ref GM_ParticleController m_ParticleCtrl;
    protected ref GM_NarrativeManager m_NarrativeMgr;
    protected ref GM_TriggerManager m_TriggerMgr;

    // ═══════════════════════════════════════════════
    // Runtime State
    // ═══════════════════════════════════════════════

    protected float m_fTimeSinceUpdate = 0;
    protected float m_fTimeSinceCleanup = 0;
    protected ref array<string> m_aRecentEvents;
    protected int m_iPlayerCount = 0;
    protected float m_fMissionTime = 0;
    protected bool m_bInitialized = false;
    protected int m_iTotalCommandsExecuted = 0;

    // Player movement tracking (Priority 4)
    protected ref map<int, ref array<vector>> m_mPlayerPositionTrails;
    protected ref map<int, vector> m_mPlayerLastPositions;
    protected ref map<int, float> m_mPlayerLastUpdateTimes;

    // ═══════════════════════════════════════════════
    // Lifecycle
    // ═══════════════════════════════════════════════

    override void OnWorldPostProcess(World world)
    {
        super.OnWorldPostProcess(world);

        // Server-only system
        if (!Replication.IsServer()) return;

        Print("[GodMode] ══════════════════════════════════════");
        Print("[GodMode] LLM God Mode Game Master — Initializing");
        Print("[GodMode] ══════════════════════════════════════");

        // Initialize all sub-managers
        m_WeatherCtrl = new GM_WeatherController();
        m_VehicleMgr = new GM_VehicleManager();
        m_SquadMgr = new GM_SquadManager();
        m_CleanupSys = new GM_CleanupSystem();
        m_SoundCtrl = new GM_SoundController();
        m_ParticleCtrl = new GM_ParticleController();
        m_NarrativeMgr = new GM_NarrativeManager();
        m_TriggerMgr = new GM_TriggerManager();
        m_aRecentEvents = {};
        m_mPlayerPositionTrails = new map<int, ref array<vector>>();
        m_mPlayerLastPositions = new map<int, vector>();
        m_mPlayerLastUpdateTimes = new map<int, float>();

        // Wire up owner references
        m_SquadMgr.SetOwner(this);
        m_SquadMgr.Init();
        m_TriggerMgr.SetOwner(this);

        // Apply limits
        m_SquadMgr.SetMaxSquads(m_iMaxSquads);
        m_VehicleMgr.SetMaxVehicles(m_iMaxVehicles);

        // Initialize REST client
        if (m_bLLMEnabled)
        {
            m_RestClient = new GM_RestClient(m_sServerURL);
            if (m_RestClient.Init())
            {
                m_RestClient.GetCallback().m_OnResponse.Insert(OnLLMResponse);
                m_RestClient.GetCallback().m_OnError.Insert(OnLLMError);
                Print("[GodMode] REST client connected → " + m_sServerURL);
            }
            else
            {
                Print("[GodMode] REST client FAILED to initialize — running in offline mode", LogLevel.WARNING);
                m_bLLMEnabled = false;
            }
        }
        else
        {
            Print("[GodMode] LLM disabled — running in offline mode");
        }

        m_bInitialized = true;

        Print("[GodMode] Update interval: " + m_fUpdateInterval + "s");
        Print("[GodMode] Max squads: " + m_iMaxSquads + " | Max vehicles: " + m_iMaxVehicles);
        Print("[GodMode] ══════════════════════════════════════");
        Print("[GodMode] Initialization complete. Awaiting players.");
        Print("[GodMode] ══════════════════════════════════════");
    }

    // ═══════════════════════════════════════════════
    // Frame Update (timer accumulator)
    // ═══════════════════════════════════════════════

    override void EOnFrame(IEntity owner, float timeSlice)
    {
        if (!Replication.IsServer() || !m_bInitialized) return;

        m_fTimeSinceUpdate += timeSlice;
        m_fTimeSinceCleanup += timeSlice;
        m_fMissionTime += timeSlice;

        // Periodic cleanup (every 10 seconds)
        if (m_fTimeSinceCleanup >= 10.0)
        {
            m_fTimeSinceCleanup = 0;
            m_CleanupSys.RunCleanupPass();
        }

        // LLM update on interval
        if (m_bLLMEnabled && m_fTimeSinceUpdate >= m_fUpdateInterval)
        {
            m_fTimeSinceUpdate = 0;
            SendSITREP();
        }
    }

    // ═══════════════════════════════════════════════
    // Player Events
    // ═══════════════════════════════════════════════

    override void OnPlayerConnected(int playerId)
    {
        super.OnPlayerConnected(playerId);
        m_iPlayerCount++;
        LogEvent("player_connected:" + playerId);
        AdjustLimitsForPlayerCount();
        Print("[GodMode] Player connected. Count: " + m_iPlayerCount);
    }

    override void OnPlayerDisconnected(int playerId, KickCauseGroup cause, int timeout)
    {
        super.OnPlayerDisconnected(playerId, cause, timeout);
        m_iPlayerCount = Math.Max(0, m_iPlayerCount - 1);
        LogEvent("player_disconnected:" + playerId);
        AdjustLimitsForPlayerCount();
    }

    override void OnPlayerKilled(int playerId, IEntity playerEntity,
                                  IEntity killerEntity, notnull Instigator killer)
    {
        super.OnPlayerKilled(playerId, playerEntity, killerEntity, killer);
        vector pos = playerEntity.GetOrigin();
        LogEvent("player_killed:pos=" + pos.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // AI Entity Events
    // ═══════════════════════════════════════════════

    override void OnControllableDestroyed(IEntity entity, IEntity killerEntity,
                                          notnull Instigator instigator)
    {
        super.OnControllableDestroyed(entity, killerEntity, instigator);

        string type = "unknown";
        if (entity.GetPrefabData())
            type = entity.GetPrefabData().GetPrefabName();

        LogEvent("unit_destroyed:" + type);
        m_CleanupSys.ScheduleCleanup(entity, 120.0);
    }

    // ═══════════════════════════════════════════════
    // Dynamic Scaling
    // ═══════════════════════════════════════════════

    protected void AdjustLimitsForPlayerCount()
    {
        int maxSquads, maxVehicles;
        float interval;

        if (m_iPlayerCount <= 0)
        {
            maxSquads = 10; maxVehicles = 5; interval = 30.0;
        }
        else if (m_iPlayerCount <= 4)
        {
            maxSquads = 15; maxVehicles = 8; interval = 30.0;
        }
        else if (m_iPlayerCount <= 12)
        {
            maxSquads = 30; maxVehicles = 15; interval = 45.0;
        }
        else
        {
            maxSquads = 50; maxVehicles = 20; interval = 60.0;
        }

        m_SquadMgr.SetMaxSquads(maxSquads);
        m_VehicleMgr.SetMaxVehicles(maxVehicles);
        m_fUpdateInterval = interval;

        Print("[GodMode] Scaled for " + m_iPlayerCount + " players: " +
              maxSquads + " squads, " + maxVehicles + " vehicles, " +
              interval + "s interval");
    }

    // ═══════════════════════════════════════════════
    // SITREP Collection & Sending
    // ═══════════════════════════════════════════════

    protected void SendSITREP()
    {
        if (!m_RestClient || !m_RestClient.IsConnected()) return;

        GM_GameState state = new GM_GameState();
        state.friendly_units = m_SquadMgr.GetFriendlyStates();
        state.enemy_units = m_SquadMgr.GetEnemyStates();
        state.contacts = m_SquadMgr.GetContactReports();
        state.players = CollectPlayerStates();
        state.current_weather = m_WeatherCtrl.GetCurrentWeatherID();
        state.time_of_day = m_WeatherCtrl.GetTimeOfDay();
        state.player_count = m_iPlayerCount;
        state.recent_events = m_aRecentEvents;
        state.current_objective = m_NarrativeMgr.GetCurrentObjectiveSummary();
        state.mission_time_seconds = m_fMissionTime;
        state.wind_speed = m_WeatherCtrl.GetWindSpeed();
        state.wind_direction = m_WeatherCtrl.GetWindDirection();
        state.total_spawned_groups = m_SquadMgr.GetSquadCount();
        state.total_spawned_vehicles = m_VehicleMgr.GetVehicleCount();

        // Terrain context (Priority 5)
        state.terrain_samples = CollectTerrainSamples(state.players, state.enemy_units);

        string json = state.Pack();
        m_RestClient.SendSITREP(json);

        // Clear event log after sending
        m_aRecentEvents.Clear();

        // Reset per-cycle combat stats
        m_SquadMgr.ResetCycleStats();
    }

    protected ref array<ref GM_PlayerState> CollectPlayerStates()
    {
        ref array<ref GM_PlayerState> players = {};
        array<int> playerIds = {};
        GetGame().GetPlayerManager().GetPlayers(playerIds);

        foreach (int pid : playerIds)
        {
            IEntity playerEntity = GetGame().GetPlayerManager().GetPlayerControlledEntity(pid);
            if (!playerEntity) continue;

            GM_PlayerState ps = new GM_PlayerState();
            ps.player_id = pid;
            ps.position = playerEntity.GetOrigin();
            ps.health_pct = 1.0;

            // Check health
            SCR_CharacterDamageManagerComponent dmg =
                SCR_CharacterDamageManagerComponent.Cast(
                    playerEntity.FindComponent(SCR_CharacterDamageManagerComponent));
            if (dmg)
                ps.health_pct = dmg.GetHealthScaled();

            // Check if in vehicle
            CompartmentAccessComponent compAccess =
                CompartmentAccessComponent.Cast(
                    playerEntity.FindComponent(CompartmentAccessComponent));
            ps.is_in_vehicle = (compAccess && compAccess.IsInCompartment());
            ps.vehicle_type = ps.is_in_vehicle ? "unknown" : "";

            // Movement vectors (Priority 4)
            vector currentPos = playerEntity.GetOrigin();

            // Calculate velocity from position delta
            vector lastPos = vector.Zero;
            float lastTime = 0;
            if (m_mPlayerLastPositions.Find(pid, lastPos) && m_mPlayerLastUpdateTimes.Find(pid, lastTime))
            {
                float dt = m_fMissionTime - lastTime;
                if (dt > 0.1)
                {
                    ps.velocity = Vector(
                        (currentPos[0] - lastPos[0]) / dt,
                        (currentPos[1] - lastPos[1]) / dt,
                        (currentPos[2] - lastPos[2]) / dt
                    );
                }
            }
            m_mPlayerLastPositions.Set(pid, currentPos);
            m_mPlayerLastUpdateTimes.Set(pid, m_fMissionTime);

            // Heading from entity angles
            vector angles = playerEntity.GetAngles();
            ps.heading = angles[0]; // yaw
            if (ps.heading < 0) ps.heading += 360;

            // Position trail (last 5 positions)
            ref array<vector> trail;
            if (!m_mPlayerPositionTrails.Find(pid, trail))
            {
                trail = new array<vector>();
                m_mPlayerPositionTrails.Set(pid, trail);
            }
            trail.Insert(currentPos);
            if (trail.Count() > 5)
                trail.Remove(0);
            ps.position_trail = trail;

            players.Insert(ps);
        }
        return players;
    }

    // ═══════════════════════════════════════════════
    // LLM Response Handler
    // ═══════════════════════════════════════════════

    protected void OnLLMResponse(string data)
    {
        m_RestClient.OnSuccess();

        if (data == "" || data == "{}")
        {
            Print("[GodMode] Empty LLM response — no orders this cycle");
            return;
        }

        GM_CommandBatch batch = new GM_CommandBatch();
        batch.ExpandFromRAW(data);

        // Execute global commands
        if (batch.weather_command != "" && batch.weather_command != "none")
            m_WeatherCtrl.SetWeather(batch.weather_command);

        if (batch.time_override >= 0)
            m_WeatherCtrl.SetTimeOfDay(batch.time_override);

        if (batch.narrative_broadcast != "")
            m_NarrativeMgr.BroadcastMessage(batch.narrative_broadcast);

        // Execute individual commands
        if (batch.commands)
        {
            int executed = 0;
            foreach (GM_Command cmd : batch.commands)
            {
                if (!cmd.IsValid())
                {
                    Print("[GodMode] Skipping invalid command: " + cmd.command_type, LogLevel.WARNING);
                    continue;
                }
                ExecuteCommand(cmd);
                executed++;
            }
            m_iTotalCommandsExecuted += executed;
            Print("[GodMode] Executed " + executed + "/" + batch.commands.Count() +
                  " commands (total: " + m_iTotalCommandsExecuted + ")");
        }
    }

    protected void OnLLMError(int errorCode)
    {
        m_RestClient.OnError();
        Print("[GodMode] LLM error " + errorCode +
              " (consecutive: " + m_RestClient.GetConsecutiveErrors() + ")" +
              " — AI continues on existing orders");
    }

    // ═══════════════════════════════════════════════
    // Command Dispatcher
    // ═══════════════════════════════════════════════

    protected void ExecuteCommand(GM_Command cmd)
    {
        Print("[GodMode] CMD: " + cmd.command_type +
              " | unit=" + cmd.unit_id +
              " | reason: " + cmd.reasoning);

        switch (cmd.command_type)
        {
            // ── Movement & Combat ──
            case GM_CommandTypes.MOVE:
                m_SquadMgr.OrderMove(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.ATTACK:
                m_SquadMgr.OrderAttack(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.DEFEND:
                m_SquadMgr.OrderDefend(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.PATROL:
                m_SquadMgr.OrderPatrol(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.SET_AMBUSH:
                m_SquadMgr.OrderAmbush(cmd.unit_id, cmd.GetTargetVector(), cmd.parameters); break;
            case GM_CommandTypes.SEEK_AND_DESTROY:
                m_SquadMgr.OrderSeekDestroy(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.RETREAT:
                m_SquadMgr.OrderRetreat(cmd.unit_id, cmd.GetTargetVector()); break;

            // ── Spawning ──
            case GM_CommandTypes.SPAWN_INFANTRY:
                m_SquadMgr.SpawnGroup(cmd.parameters, cmd.GetTargetVector()); break;
            case GM_CommandTypes.SPAWN_VEHICLE:
                m_VehicleMgr.SpawnVehicle(cmd.parameters, cmd.GetTargetVector()); break;
            case GM_CommandTypes.SPAWN_REINFORCEMENTS:
                m_SquadMgr.SpawnReinforcements(cmd.parameters, cmd.GetTargetVector()); break;

            // ── Environment ──
            case GM_CommandTypes.SET_WEATHER:
                m_WeatherCtrl.SetWeather(cmd.parameters); break;
            case GM_CommandTypes.SET_TIME:
                m_WeatherCtrl.SetTimeOfDay(cmd.parameters.ToFloat()); break;
            case GM_CommandTypes.SET_WIND:
                m_WeatherCtrl.SetWind(cmd.parameters); break;
            case GM_CommandTypes.THUNDER_NEAR:
                m_SoundCtrl.PlayThunderNear(cmd.GetTargetVector()); break;
            case GM_CommandTypes.PLAY_SOUND:
                m_SoundCtrl.PlaySoundAt(cmd.parameters, cmd.GetTargetVector()); break;

            // ── Effects ──
            case GM_CommandTypes.EXPLOSION:
                m_ParticleCtrl.SpawnExplosion(cmd.GetTargetVector(), cmd.parameters); break;
            case GM_CommandTypes.SMOKE:
                m_ParticleCtrl.SpawnSmoke(cmd.GetTargetVector(), cmd.parameters); break;
            case GM_CommandTypes.FLARE:
                m_ParticleCtrl.SpawnFlare(cmd.GetTargetVector()); break;

            // ── Cleanup ──
            case GM_CommandTypes.CLEANUP_AREA:
                m_CleanupSys.CleanupRadius(cmd.GetTargetVector(), cmd.parameters.ToFloat()); break;
            case GM_CommandTypes.CLEANUP_DEAD:
                m_CleanupSys.CleanupAllDead(); break;
            case GM_CommandTypes.DESPAWN_UNIT:
                m_SquadMgr.DespawnGroup(cmd.unit_id); break;

            // ── Narrative ──
            case GM_CommandTypes.RADIO_MESSAGE:
                m_NarrativeMgr.SendRadioMessage(cmd.narrative, cmd.parameters); break;
            case GM_CommandTypes.OBJECTIVE_CREATE:
                m_NarrativeMgr.CreateObjective(cmd.parameters, cmd.narrative, cmd.GetTargetVector()); break;
            case GM_CommandTypes.OBJECTIVE_COMPLETE:
                m_NarrativeMgr.CompleteObjective(cmd.parameters); break;

            default:
                Print("[GodMode] Unknown command type: " + cmd.command_type, LogLevel.WARNING);
        }

        // Broadcast narrative flavor text if present (and not already a narrative command)
        if (cmd.narrative != "" &&
            cmd.command_type != GM_CommandTypes.RADIO_MESSAGE &&
            cmd.command_type != GM_CommandTypes.OBJECTIVE_CREATE)
        {
            m_NarrativeMgr.BroadcastMessage(cmd.narrative);
        }
    }

    // ═══════════════════════════════════════════════
    // Public Event Logger
    // ═══════════════════════════════════════════════

    void LogEvent(string evt)
    {
        if (!m_aRecentEvents) return;

        // Ring buffer — cap at 50 events
        if (m_aRecentEvents.Count() > 50)
            m_aRecentEvents.Remove(0);

        m_aRecentEvents.Insert(evt);
    }

    // ═══════════════════════════════════════════════
    // Public Accessors (for other systems)
    // ═══════════════════════════════════════════════

    GM_WeatherController GetWeatherController() { return m_WeatherCtrl; }
    GM_VehicleManager GetVehicleManager() { return m_VehicleMgr; }
    GM_SquadManager GetSquadManager() { return m_SquadMgr; }
    GM_NarrativeManager GetNarrativeManager() { return m_NarrativeMgr; }
    GM_TriggerManager GetTriggerManager() { return m_TriggerMgr; }
    float GetMissionTime() { return m_fMissionTime; }
    int GetPlayerCount() { return m_iPlayerCount; }
    bool IsLLMEnabled() { return m_bLLMEnabled; }
}
