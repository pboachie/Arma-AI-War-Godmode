// GM_GodModeComponent.c — AI War: Autonomous LLM Battlefield Controller
// Attach this component to your GameMode entity in the Workbench.
// 100% LLM-managed — no scripted events, no human admin intervention.
// The AI reads the battlefield, makes every decision, and runs the entire war.

[EntityEditorProps(category: "GameScripted/GodMode", description: "AI War — 100% LLM-managed autonomous battlefield controller")]
class GM_GodModeComponentClass : SCR_BaseGameModeComponentClass {}

class GM_GodModeComponent : SCR_BaseGameModeComponent
{
    // ═══════════════════════════════════════════════
    // Configuration (editable in Workbench)
    // ═══════════════════════════════════════════════

    [Attribute("30", UIWidgets.Slider, desc: "Seconds between LLM updates", params: "5 300 1")]
    protected float m_fUpdateInterval;

    [Attribute("http://127.0.0.1:8080", desc: "Dedicated-server middleware URL")]
    protected string m_sServerURL;

    [Attribute("1", desc: "Enable dedicated-server middleware communication (0=offline mode)")]
    protected bool m_bLLMEnabled;

    [Attribute("30", desc: "Max AI squads allowed")]
    protected int m_iMaxSquads;

    [Attribute("20", desc: "Max vehicles allowed")]
    protected int m_iMaxVehicles;

    [Attribute("", UIWidgets.EditBox, desc: "Legacy fallback neutral anchors. Prefer GM_SpawnAnchorComponent markers. Format: Name|x|z|radius;Name2|x|z|radius")]
    protected string m_sNeutralBaseAnchors;

    [Attribute("", UIWidgets.EditBox, desc: "Legacy fallback road junction anchors. Prefer GM_SpawnAnchorComponent markers. Format: Name|x|z|radius;Name2|x|z|radius")]
    protected string m_sRoadJunctionAnchors;

    [Attribute("", UIWidgets.EditBox, desc: "Legacy fallback roadblock anchors. Prefer GM_SpawnAnchorComponent markers. Format: Name|x|z|radius;Name2|x|z|radius")]
    protected string m_sRoadblockAnchors;

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
    protected ref GM_MissionEngine m_MissionEngine;
    protected ref GM_AmbientBattlefield m_AmbientBattlefield;

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
    protected bool m_bLLMSchedulerStarted = false;
    protected bool m_bWarmupSITREPQueued = false;

    // Player movement tracking (Priority 4)
    protected ref map<int, ref array<vector>> m_mPlayerPositionTrails;
    protected ref map<int, vector> m_mPlayerLastPositions;
    protected ref map<int, float> m_mPlayerLastUpdateTimes;

    // ═══════════════════════════════════════════════
    // Lifecycle
    // ═══════════════════════════════════════════════

    override void OnPostInit(IEntity owner)
    {
        super.OnPostInit(owner);

        if (owner)
            SetEventMask(owner, EntityEvent.FRAME);
    }

    override void OnWorldPostProcess(World world)
    {
        super.OnWorldPostProcess(world);

        // Server-only system
        if (!Replication.IsServer()) return;

        Print("[GodMode] =============================================");
        Print("[GodMode] AI War game mode starting");
        Print("[GodMode] =============================================");

        // Initialize all sub-managers
        m_WeatherCtrl = new GM_WeatherController();
        m_VehicleMgr = new GM_VehicleManager();
        m_SquadMgr = new GM_SquadManager();
        m_CleanupSys = new GM_CleanupSystem();
        m_SoundCtrl = new GM_SoundController();
        m_ParticleCtrl = new GM_ParticleController();
        m_NarrativeMgr = new GM_NarrativeManager();
        m_TriggerMgr = new GM_TriggerManager();
        m_MissionEngine = new GM_MissionEngine();
        m_MissionEngine.SetOwner(this);
        m_aRecentEvents = {};
        m_mPlayerPositionTrails = new map<int, ref array<vector>>();
        m_mPlayerLastPositions = new map<int, vector>();
        m_mPlayerLastUpdateTimes = new map<int, float>();

        // Initialize ambient battlefield system
        m_AmbientBattlefield = new GM_AmbientBattlefield();
        m_AmbientBattlefield.Init(m_SoundCtrl, m_ParticleCtrl);

        // Wire up owner references
        m_SquadMgr.SetOwner(this);
        m_SquadMgr.Init();
        m_VehicleMgr.Init();
        m_TriggerMgr.SetOwner(this);

        // Apply limits
        m_SquadMgr.SetMaxSquads(m_iMaxSquads);
        m_VehicleMgr.SetMaxVehicles(m_iMaxVehicles);

        if (m_WeatherCtrl.HasManager())
            Print("[GodMode] Weather/time controls available");
        else
            Print("[GodMode] Weather/time controls unavailable in this world — weather commands will be skipped", LogLevel.WARNING);

        // Initialize REST client
        if (m_bLLMEnabled)
        {
            m_RestClient = new GM_RestClient(m_sServerURL);
            if (m_RestClient.Init())
            {
                m_RestClient.GetCallback().m_OnResponse.Insert(OnLLMResponse);
                m_RestClient.GetCallback().m_OnError.Insert(OnLLMError);
                Print("[GodMode] REST client initialized -> " + m_RestClient.GetBaseURL() +
                      " [" + m_RestClient.GetRevisionTag() + "]");
            }
            else
            {
                Print("[GodMode] REST client FAILED to initialize -- running in offline mode", LogLevel.WARNING);
                m_bLLMEnabled = false;
            }
        }
        else
        {
            Print("[GodMode] Middleware communication disabled — running in offline mode");
        }

        m_bInitialized = true;

        if (m_bLLMEnabled)
            StartLLMScheduler();

        Print("[GodMode] Update interval: " + m_fUpdateInterval + "s");
        Print("[GodMode] Max squads: " + m_iMaxSquads + " | Max vehicles: " + m_iMaxVehicles);
        Print("[GodMode] =====================================");
        Print("[GodMode] Initialization complete. Awaiting players.");
        Print("[GodMode] =====================================");
    }

    // ═══════════════════════════════════════════════
    // Frame Update (timer accumulator)
    // ═══════════════════════════════════════════════

    override void EOnFrame(IEntity owner, float timeSlice)
    {
        if (!Replication.IsServer() || !m_bInitialized) return;

        m_fTimeSinceCleanup += timeSlice;
        m_fMissionTime += timeSlice;

        if (m_WeatherCtrl)
            m_WeatherCtrl.TickProgression(timeSlice);

        // Periodic cleanup (every 10 seconds)
        if (m_fTimeSinceCleanup >= 10.0)
        {
            m_fTimeSinceCleanup = 0;
            m_CleanupSys.UpdateGameTime(m_fMissionTime);
            m_CleanupSys.RunCleanupPass();
        }

        // Tick the ambient battlefield system
        if (m_AmbientBattlefield)
        {
            // Use average player position as center for ambient effects
            vector playerCenter = GetAveragePlayerPosition();
            m_AmbientBattlefield.Tick(timeSlice, playerCenter);
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

        if (m_bLLMEnabled && !m_bWarmupSITREPQueued && GetGame())
        {
            m_bWarmupSITREPQueued = true;
            GetGame().GetCallqueue().CallLater(SendWarmupSITREP, 5000, false);
            Print("[GodMode] Warm-up SITREP queued in 5s");
        }

        Print("[GodMode] Player connected. Count: " + m_iPlayerCount + " [GM_REV_20260321_B]");
    }

    void OnPlayerDisconnected(int playerId, KickCauseGroup cause, int timeout)
    {
        m_iPlayerCount = Math.Max(0, m_iPlayerCount - 1);
        LogEvent("player_disconnected:" + playerId);

        // Clean up player tracking data to prevent memory leaks
        m_mPlayerPositionTrails.Remove(playerId);
        m_mPlayerLastPositions.Remove(playerId);
        m_mPlayerLastUpdateTimes.Remove(playerId);

        AdjustLimitsForPlayerCount();
    }

    void OnPlayerKilled(int playerId, IEntity playerEntity,
                         IEntity killerEntity, notnull Instigator killer)
    {
        vector pos = playerEntity.GetOrigin();
        LogEvent("player_killed:pos=" + pos.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // AI Entity Events
    // ═══════════════════════════════════════════════

    void OnControllableDestroyed(IEntity entity, IEntity killerEntity,
                                  notnull Instigator instigator)
    {

        string type = "unknown";
        if (entity.GetPrefabData())
            type = entity.GetPrefabData().GetPrefabName();

        LogEvent("unit_destroyed:" + type);
        m_CleanupSys.ScheduleCleanupById(entity.GetID(), 120.0);
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
            maxSquads = 20; maxVehicles = 10; interval = 25.0;
        }
        else if (m_iPlayerCount <= 4)
        {
            maxSquads = 30; maxVehicles = 15; interval = 20.0;
        }
        else if (m_iPlayerCount <= 12)
        {
            maxSquads = 50; maxVehicles = 25; interval = 25.0;
        }
        else
        {
            maxSquads = 70; maxVehicles = 30; interval = 30.0;
        }

        m_SquadMgr.SetMaxSquads(maxSquads);
        m_VehicleMgr.SetMaxVehicles(maxVehicles);
        m_fUpdateInterval = interval;

        Print("[GodMode] Scaled for " + m_iPlayerCount + " players: " +
              maxSquads + " squads, " + maxVehicles + " vehicles, " +
              interval + "s interval");
    }

    protected void StartLLMScheduler()
    {
        if (m_bLLMSchedulerStarted || !GetGame())
            return;

        m_bLLMSchedulerStarted = true;
        Print("[GodMode] LLM scheduler armed");
        ScheduleNextSITREP();
    }

    protected void ScheduleNextSITREP()
    {
        if (!GetGame() || !m_bLLMEnabled)
            return;

        int delayMs = Math.Max(5000, Math.Round(m_fUpdateInterval * 1000.0));
        Print("[GodMode] Next SITREP scheduled in " + (delayMs / 1000) + "s");
        GetGame().GetCallqueue().CallLater(PerformScheduledSITREP, delayMs, false);
    }

    protected void PerformScheduledSITREP()
    {
        if (Replication.IsServer() && m_bInitialized && m_bLLMEnabled)
            SendSITREP();

        ScheduleNextSITREP();
    }

    protected void SendWarmupSITREP()
    {
        if (!Replication.IsServer() || !m_bInitialized || !m_bLLMEnabled)
            return;

        Print("[GodMode] Sending warm-up SITREP");
        SendSITREP();
    }

    // ═══════════════════════════════════════════════
    // SITREP Collection & Sending
    // ═══════════════════════════════════════════════

    protected void SendSITREP()
    {
        if (!m_RestClient || !m_RestClient.IsConnected()) return;

        Print("[GodMode] Sending SITREP -> " + m_RestClient.GetBaseURL());

        GM_GameState state = new GM_GameState();
        state.friendly_units = m_SquadMgr.GetFriendlyStates();
        state.enemy_units = m_SquadMgr.GetEnemyStates();
        state.contacts = m_SquadMgr.GetContactReports();
        state.players = CollectPlayerStates();
        state.current_weather = m_WeatherCtrl.GetCurrentWeatherID();
        state.weather_controls_available = m_WeatherCtrl.HasManager();
        state.time_of_day = m_WeatherCtrl.GetTimeOfDay();
        state.player_count = m_iPlayerCount;
        state.recent_events = m_aRecentEvents;
        state.current_objective = m_NarrativeMgr.GetCurrentObjectiveSummary();
        state.mission_time_seconds = m_fMissionTime;
        state.wind_speed = m_WeatherCtrl.GetWindSpeed();
        state.wind_direction = m_WeatherCtrl.GetWindDirection();
        state.total_spawned_groups = m_SquadMgr.GetSquadCount();
        state.total_spawned_vehicles = m_VehicleMgr.GetVehicleCount();
        state.weather_tactical_context = BuildWeatherTacticalContext();

        // Terrain context (Priority 5)
        state.terrain_samples = CollectTerrainSamples(state.players, state.enemy_units);
        state.spawn_anchors = CollectConfiguredSpawnAnchors();

        // Mission engine state
        state.mission_state = m_MissionEngine.GetMissionState();

        // Ambient battlefield state for SITREP
        if (m_AmbientBattlefield)
        {
            GM_AmbientState ambState = new GM_AmbientState();
            ambState.ambient_intensity = m_AmbientBattlefield.GetIntensity();
            ambState.active_fires = m_AmbientBattlefield.GetActiveFires();
            ambState.active_smoke_columns = m_AmbientBattlefield.GetActiveSmokeColumns();
            ambState.artillery_active = m_AmbientBattlefield.IsArtilleryActive();
            ambState.battlefield_noise_level = m_AmbientBattlefield.GetNoiseLevel();
            state.ambient_state = ambState;
        }

        state.Pack();
        string json = state.AsString();
        if (!m_RestClient.SendSITREP(json))
            return;

        // Clear event log after sending
        m_aRecentEvents.Clear();

        // Reset per-cycle combat stats
        m_SquadMgr.ResetCycleStats();
    }

    protected GM_WeatherTacticalContext BuildWeatherTacticalContext()
    {
        GM_WeatherTacticalContext context = new GM_WeatherTacticalContext();

        float timeOfDay = 12.0;
        float windSpeed = 0.0;
        float windDirection = 0.0;
        string weatherId = "clear";

        if (m_WeatherCtrl)
        {
            timeOfDay = m_WeatherCtrl.GetTimeOfDay();
            windSpeed = m_WeatherCtrl.GetWindSpeed();
            windDirection = m_WeatherCtrl.GetWindDirection();
            weatherId = m_WeatherCtrl.GetCurrentWeatherID();
        }

        string weatherLower = weatherId;
        weatherLower.ToLower();

        context.visibility_range_meters = 1000.0;
        context.audio_masking = "low";
        context.sniper_effectiveness = "high";
        context.air_support_available = true;
        context.glare_advantage_direction = FormatCardinalDirection(windDirection);
        context.ambient_light_level = "day";
        context.is_night = false;
        context.is_twilight = false;
        context.moon_phase = "unknown";
        context.sun_bearing = Math.Mod(timeOfDay / 24.0 * 360.0, 360.0);

        if (timeOfDay < 5.0 || timeOfDay > 21.0)
        {
            context.is_night = true;
            context.ambient_light_level = "night";
            context.visibility_range_meters = 180.0;
            context.sniper_effectiveness = "low";
            context.air_support_available = false;
        }
        else if ((timeOfDay >= 5.0 && timeOfDay < 7.0) || (timeOfDay > 19.0 && timeOfDay <= 21.0))
        {
            context.is_twilight = true;
            context.ambient_light_level = "twilight";
            context.visibility_range_meters = 450.0;
            context.sniper_effectiveness = "medium";
        }

        if (weatherLower.Contains("fog"))
        {
            context.visibility_range_meters = 120.0;
            context.audio_masking = "high";
            context.sniper_effectiveness = "low";
            context.air_support_available = false;
        }
        else if (weatherLower.Contains("storm") || weatherLower.Contains("rain"))
        {
            context.visibility_range_meters = Math.Min(context.visibility_range_meters, 350.0);
            context.audio_masking = "high";
            context.sniper_effectiveness = "low";
            context.air_support_available = false;
        }
        else if (weatherLower.Contains("overcast") || weatherLower.Contains("cloud"))
        {
            context.visibility_range_meters = Math.Min(context.visibility_range_meters, 700.0);
            context.audio_masking = "medium";
            context.sniper_effectiveness = "medium";
        }

        if (windSpeed > 8.0)
            context.audio_masking = "high";
        else if (windSpeed > 3.0 && context.audio_masking == "low")
            context.audio_masking = "medium";

        return context;
    }

    protected string FormatCardinalDirection(float degrees)
    {
        float normalized = Math.Mod(degrees, 360.0);
        if (normalized < 0)
            normalized = normalized + 360.0;

        if (normalized < 22.5 || normalized >= 337.5)
            return "north";
        if (normalized < 67.5)
            return "north-east";
        if (normalized < 112.5)
            return "east";
        if (normalized < 157.5)
            return "south-east";
        if (normalized < 202.5)
            return "south";
        if (normalized < 247.5)
            return "south-west";
        if (normalized < 292.5)
            return "west";
        return "north-west";
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
            ps.vehicle_type = "";
            if (ps.is_in_vehicle)
                ps.vehicle_type = "unknown";

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
    // Helper: Average player position for ambient effects
    // ═══════════════════════════════════════════════

    protected vector GetAveragePlayerPosition()
    {
        vector sum = vector.Zero;
        int count = 0;

        array<int> playerIds = {};
        GetGame().GetPlayerManager().GetPlayers(playerIds);

        foreach (int pid : playerIds)
        {
            IEntity ent = GetGame().GetPlayerManager().GetPlayerControlledEntity(pid);
            if (!ent) continue;
            vector pos = ent.GetOrigin();
            sum[0] = sum[0] + pos[0];
            sum[1] = sum[1] + pos[1];
            sum[2] = sum[2] + pos[2];
            count++;
        }

        if (count == 0) return vector.Zero;

        return Vector(sum[0] / count, sum[1] / count, sum[2] / count);
    }

    // ═══════════════════════════════════════════════
    // LLM Response Handler
    // ═══════════════════════════════════════════════

    protected void OnLLMResponse(string data)
    {
        m_RestClient.OnSuccess();

        if (data == "" || data == "{}")
        {
            Print("[GodMode] Empty LLM response -- no orders this cycle");
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
            int count = batch.commands.Count();
            if (count == 0)
            {
                // Server returns empty commands during async warmup (first 1-2 SITREPs)
                // or when the LLM pipeline is still processing. This is expected.
                Print("[GodMode] No middleware commands this cycle");
                return;
            }

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
            Print("[GodMode] Executed " + executed + "/" + count +
                  " commands (total: " + m_iTotalCommandsExecuted + ")");
        }
    }

    protected void OnLLMError(int errorCode)
    {
        m_RestClient.OnError();
        Print("[GodMode] LLM error " + errorCode +
              " (consecutive: " + m_RestClient.GetConsecutiveErrors() + ")" +
              " -- AI continues on existing orders");
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

            // ── Advanced Tactical (Squad Manager) ──
            case GM_CommandTypes.SUPPRESS:
                m_SquadMgr.OrderSuppress(cmd.unit_id, cmd.GetTargetVector(), cmd.parameters); break;
            case GM_CommandTypes.OVERWATCH:
                m_SquadMgr.OrderOverwatch(cmd.unit_id, cmd.GetTargetVector(), cmd.parameters); break;
            case GM_CommandTypes.SILENT_MOVE:
                m_SquadMgr.OrderSilentMove(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.GARRISON:
                m_SquadMgr.OrderGarrison(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.ESTABLISH_OP:
                m_SquadMgr.OrderEstablishOP(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.FIGHTING_WITHDRAWAL:
                m_SquadMgr.OrderFightingWithdrawal(cmd.unit_id, cmd.parameters); break;
            case GM_CommandTypes.PATROL_CLOVERLEAF:
                m_SquadMgr.OrderPatrolCloverleaf(cmd.unit_id, cmd.GetTargetVector(), 200); break;
            case GM_CommandTypes.PATROL_ZIGZAG:
            {
                // Use squad's current position as start, target_position as destination
                SCR_AIGroup zigGroup = m_SquadMgr.GetGroupPublic(cmd.unit_id);
                vector zigStart = cmd.GetTargetVector();
                if (zigGroup)
                    zigStart = zigGroup.GetOrigin();
                m_SquadMgr.OrderPatrolZigzag(cmd.unit_id, zigStart, cmd.GetTargetVector(), 80);
                break;
            }

            // ── Compound Tactical (flanking maneuvers with lateral offset) ──
            case GM_CommandTypes.FLANK_LEFT:
                ExecuteFlankManeuver(cmd, -1); break;
            case GM_CommandTypes.FLANK_RIGHT:
                ExecuteFlankManeuver(cmd, 1); break;
            case GM_CommandTypes.BREACH:
                m_SquadMgr.OrderAttack(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.SET_KEYHOLE:
                m_SquadMgr.OrderDefend(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.BOUNDING_OVERWATCH:
                m_SquadMgr.OrderMove(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.COORDINATED_OP:
                ExecuteCoordinatedOperation(cmd); break;

            // ── Vehicle Tactical ──
            case GM_CommandTypes.HULL_DOWN:
                m_VehicleMgr.OrderVehicleDefend(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.OVERWATCH_POSITION:
                m_VehicleMgr.OrderVehicleDefend(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.DISMOUNT_ASSAULT:
                m_SquadMgr.OrderAttack(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.CAS_RUN:
                ExecuteCASRun(cmd); break;
            case GM_CommandTypes.TROOP_INSERT:
                ExecuteTroopInsert(cmd); break;
            case GM_CommandTypes.CONVOY_FORM:
                ExecuteConvoyForm(cmd); break;
            case GM_CommandTypes.VEHICLE_SCREEN:
                ExecuteVehicleScreen(cmd); break;
            case GM_CommandTypes.AIR_PATROL:
                Print("[GodMode] Air patrol requested -- future implementation (logged only)"); break;

            // ── Spawning ──
            case GM_CommandTypes.SPAWN_INFANTRY:
                m_SquadMgr.SpawnGroup(cmd.parameters, cmd.GetTargetVector()); break;
            case GM_CommandTypes.SPAWN_VEHICLE:
                m_VehicleMgr.SpawnCrewedVehicle(cmd.parameters, cmd.GetTargetVector()); break;
            case GM_CommandTypes.SPAWN_REINFORCEMENTS:
                m_SquadMgr.SpawnReinforcements(cmd.parameters, cmd.GetTargetVector()); break;

            // ── Environment ──
            case GM_CommandTypes.SET_WEATHER:
                m_WeatherCtrl.SetWeather(cmd.parameters); break;
            case GM_CommandTypes.SET_TIME:
            {
                float t = cmd.parameters.ToFloat();
                if (t >= 0 && t <= 24)
                    m_WeatherCtrl.SetTimeOfDay(t);
                break;
            }
            case GM_CommandTypes.SET_WIND:
                m_WeatherCtrl.SetWind(cmd.parameters); break;
            case GM_CommandTypes.THUNDER_NEAR:
                m_SoundCtrl.PlayThunderNear(cmd.GetTargetVector()); break;
            case GM_CommandTypes.PLAY_SOUND:
                m_SoundCtrl.PlaySoundAt(cmd.parameters, cmd.GetTargetVector()); break;

            // ── Advanced Environment ──
            case GM_CommandTypes.SET_WEATHER_PROGRESSION:
                ExecuteWeatherProgression(cmd); break;
            case GM_CommandTypes.SET_TACTICAL_LIGHTING:
                m_WeatherCtrl.SetTacticalLighting(cmd.parameters, 60); break;
            case GM_CommandTypes.SET_AMBIENT_INTENSITY:
            {
                int amb = cmd.parameters.ToInt();
                if (m_AmbientBattlefield && amb >= 0 && amb <= 3)
                    m_AmbientBattlefield.SetIntensity(amb);
                break;
            }
            case GM_CommandTypes.ARTILLERY_BARRAGE:
                ExecuteArtilleryBarrage(cmd); break;
            case GM_CommandTypes.BUILDING_FIRE:
                ExecuteBuildingFire(cmd); break;
            case GM_CommandTypes.DISTANT_COMBAT:
                ExecuteDistantCombat(cmd); break;
            case GM_CommandTypes.ENVIRONMENT_MOOD:
                m_WeatherCtrl.SetEnvironmentMood(cmd.parameters); break;

            // ── Effects ──
            case GM_CommandTypes.EXPLOSION:
                m_ParticleCtrl.SpawnExplosion(cmd.GetTargetVector(), cmd.parameters); break;
            case GM_CommandTypes.SMOKE:
                m_ParticleCtrl.SpawnSmoke(cmd.GetTargetVector(), cmd.parameters); break;
            case GM_CommandTypes.FLARE:
                m_ParticleCtrl.SpawnFlare(cmd.GetTargetVector()); break;

            // ── Cleanup ──
            case GM_CommandTypes.CLEANUP_AREA:
            {
                float r = cmd.parameters.ToFloat();
                if (r <= 0) r = 100;
                m_CleanupSys.CleanupRadius(cmd.GetTargetVector(), r);
                break;
            }
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
                m_NarrativeMgr.CompleteObjective(cmd.parameters);
                m_MissionEngine.RecordObjectiveComplete(cmd.parameters);
                break;
            case GM_CommandTypes.OBJECTIVE_FAIL:
                m_NarrativeMgr.FailObjective(cmd.parameters);
                m_MissionEngine.RecordObjectiveFailed(cmd.parameters);
                break;

            // ── Narrative Advanced ──
            case GM_CommandTypes.RADIO_COMMS:
                m_NarrativeMgr.ShowNotification(cmd.parameters); break;
            case GM_CommandTypes.INTEL_FEED:
                m_NarrativeMgr.ShowNotification(cmd.parameters); break;
            case GM_CommandTypes.FACTION_COMMS:
                m_NarrativeMgr.ShowNotification(cmd.parameters); break;
            case GM_CommandTypes.INTERCEPTED_COMMS:
                m_NarrativeMgr.ShowNotification(cmd.parameters); break;
            case GM_CommandTypes.PLAYER_ACK:
                m_NarrativeMgr.ShowNotification(cmd.parameters); break;
            case GM_CommandTypes.PACING_DIRECTIVE:
                Print("[GodMode] Pacing directive (internal LLM): " + cmd.parameters); break;

            // ── Fire Support ──
            case GM_CommandTypes.MORTAR_STRIKE:
                ExecuteMortarStrike(cmd); break;
            case GM_CommandTypes.AIR_SUPPORT:
                ExecuteAirSupport(cmd); break;

            // ── Logistics ──
            case GM_CommandTypes.SUPPLY_DROP:
                ExecuteSupplyDrop(cmd); break;
            case GM_CommandTypes.SPAWN_CONVOY:
                ExecuteSpawnConvoy(cmd); break;

            // ── Fortification ──
            case GM_CommandTypes.ESTABLISH_CHECKPOINT:
                ExecuteEstablishCheckpoint(cmd); break;
            case GM_CommandTypes.LAY_MINES:
                ExecuteLayMines(cmd); break;
            case GM_CommandTypes.SET_ROADBLOCK:
                ExecuteSetRoadblock(cmd); break;

            // ── Markers ──
            case GM_CommandTypes.SPAWN_MARKER:
                ExecuteSpawnMarker(cmd); break;

            // ── Meta / Mission Control ──
            case GM_CommandTypes.SET_ALERT_LEVEL:
                m_MissionEngine.SetAlertLevel(cmd.parameters); break;
            case GM_CommandTypes.ORDER_FORMATION:
                // Formation is set per-command via cmd.formation field;
                // this command type applies it explicitly
                Print("[GodMode] Formation order: " + cmd.unit_id + " → " + cmd.formation);
                break;
            case GM_CommandTypes.START_SCENARIO:
                ExecuteStartScenario(cmd); break;
            case GM_CommandTypes.END_SCENARIO:
                m_MissionEngine.EndScenario(cmd.parameters); break;

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
    // Terrain Context Collection (Priority 5)
    // ═══════════════════════════════════════════════

    protected ref array<ref GM_TerrainSample> CollectTerrainSamples(
        array<ref GM_PlayerState> players,
        array<ref GM_UnitState> enemyUnits)
    {
        ref array<ref GM_TerrainSample> samples = {};
        ref array<vector> samplePoints = {};

        // Sample around each player position
        foreach (GM_PlayerState p : players)
        {
            samplePoints.Insert(p.position);
            // Add cardinal direction samples 100m out
            float offsets[] = {100, -100};
            foreach (float ox : offsets)
            {
                samplePoints.Insert(Vector(p.position[0] + ox, 0, p.position[2]));
                samplePoints.Insert(Vector(p.position[0], 0, p.position[2] + ox));
            }
        }

        // Sample at key unit positions
        foreach (GM_UnitState u : enemyUnits)
        {
            samplePoints.Insert(u.position);
        }

        // Sample midpoints between players and nearest units (likely engagement zones)
        foreach (GM_PlayerState p : players)
        {
            foreach (GM_UnitState u : enemyUnits)
            {
                float dist = vector.Distance(p.position, u.position);
                if (dist < 500) // Only sample nearby engagement zones
                {
                    vector mid = Vector(
                        (p.position[0] + u.position[0]) * 0.5,
                        0,
                        (p.position[2] + u.position[2]) * 0.5
                    );
                    samplePoints.Insert(mid);
                }
            }
        }

        // Generate terrain samples (cap at 30 to limit payload)
        int count = 0;
        foreach (vector pt : samplePoints)
        {
            if (count >= 30) break;

            GM_TerrainSample sample = new GM_TerrainSample();
            sample.position = pt;

            // Get terrain elevation
            float surfaceY = GetGame().GetWorld().GetSurfaceY(pt[0], pt[2]);
            sample.elevation = surfaceY;
            sample.position[1] = surfaceY;

            // Classify terrain type using trace queries
            sample.terrain_type = ClassifyTerrain(pt, surfaceY);
            sample.cover_density = EstimateCoverDensity(pt);

            samples.Insert(sample);
            count++;
        }

        return samples;
    }

    protected string ClassifyTerrain(vector pos, float elevation)
    {
        // Check for road nearby using trace
        // Simple heuristic: flat areas near roads, elevation changes = hills
        float nearbyY1 = GetGame().GetWorld().GetSurfaceY(pos[0] + 5, pos[2]);
        float nearbyY2 = GetGame().GetWorld().GetSurfaceY(pos[0], pos[2] + 5);
        float slopeDelta = Math.AbsFloat(elevation - nearbyY1) + Math.AbsFloat(elevation - nearbyY2);

        if (slopeDelta > 3.0)
            return "hill";

        // Check for objects nearby (buildings, trees) via trace
        vector start = pos;
        start[1] = elevation + 1.5;
        vector end = start;
        end[1] = elevation + 15; // Check upward for canopy/buildings

        TraceParam trace = new TraceParam();
        trace.Start = start;
        trace.End = end;
        trace.Flags = TraceFlags.WORLD;

        float hitFraction = GetGame().GetWorld().TraceMove(trace, null);
        if (hitFraction < 1.0)
        {
            // Something above — likely trees or buildings
            if (elevation < 2.0)
                return "urban";
            else
                return "forest";
        }

        // Check if water
        if (elevation < 0.5)
            return "water";

        return "open";
    }

    protected float EstimateCoverDensity(vector pos)
    {
        // Cast multiple horizontal traces to estimate nearby cover
        int hits = 0;
        int traces = 8;
        float radius = 25.0;
        float elevation = GetGame().GetWorld().GetSurfaceY(pos[0], pos[2]);

        for (int i = 0; i < traces; i++)
        {
            float angle = i * (360.0 / traces) * Math.DEG2RAD;
            vector start = pos;
            start[1] = elevation + 1.0;
            vector end = Vector(
                pos[0] + Math.Cos(angle) * radius,
                elevation + 1.0,
                pos[2] + Math.Sin(angle) * radius
            );

            TraceParam trace = new TraceParam();
            trace.Start = start;
            trace.End = end;
            trace.Flags = TraceFlags.WORLD | TraceFlags.ENTS;

            float hitFraction = GetGame().GetWorld().TraceMove(trace, null);
            if (hitFraction < 0.9)
                hits++;
        }

        return hits / (float)traces;
    }

    protected ref array<ref GM_SpawnAnchor> CollectConfiguredSpawnAnchors()
    {
        ref array<ref GM_SpawnAnchor> anchors = {};

        bool hasComponentAnchors = AppendComponentAnchors(anchors) > 0;
        if (!hasComponentAnchors)
        {
            AppendConfiguredAnchors(m_sNeutralBaseAnchors, "neutral_base", "neutral", 180.0, anchors);
            AppendConfiguredAnchors(m_sRoadJunctionAnchors, "road_junction", "unassigned", 120.0, anchors);
            AppendConfiguredAnchors(m_sRoadblockAnchors, "roadblock_candidate", "unassigned", 90.0, anchors);
        }

        AppendMilitaryBaseAnchors(anchors);

        if (m_NarrativeMgr)
            m_NarrativeMgr.AppendObjectiveAnchors(anchors);

        AppendPlayerTrailAnchors(anchors);
        FinalizeSpawnAnchors(anchors);

        return anchors;
    }

    protected int AppendComponentAnchors(array<ref GM_SpawnAnchor> outAnchors)
    {
        if (!outAnchors)
            return 0;

        array<GM_SpawnAnchorComponent> components = {};
        GM_SpawnAnchorComponent.GetRegistered(components);

        int inserted = 0;
        foreach (GM_SpawnAnchorComponent component : components)
        {
            if (!component)
                continue;

            GM_SpawnAnchor anchor = new GM_SpawnAnchor();
            anchor.anchor_id = "component_anchor_" + (outAnchors.Count() + 1);
            anchor.name = component.GetAnchorName();
            anchor.position = component.GetAnchorPosition();
            anchor.anchor_type = SanitizeAnchorType(component.GetAnchorType());
            anchor.control_state = SanitizeControlState(component.GetControlState());
            anchor.tactical_radius = component.GetTacticalRadius();
            anchor.priority = component.GetPriority();
            anchor.notes = component.GetNotes();
            if (anchor.notes == "")
                anchor.notes = GetSpawnAnchorNotes(anchor.anchor_type);

            if (UpsertSpawnAnchor(anchor, outAnchors))
                inserted++;
        }

        return inserted;
    }

    protected void AppendMilitaryBaseAnchors(array<ref GM_SpawnAnchor> outAnchors)
    {
        if (!outAnchors)
            return;

        SCR_MilitaryBaseSystem militaryBaseSystem = SCR_MilitaryBaseSystem.GetInstance();
        if (!militaryBaseSystem)
            return;

        array<SCR_MilitaryBaseComponent> bases = {};
        militaryBaseSystem.GetBases(bases);

        foreach (SCR_MilitaryBaseComponent baseComponent : bases)
        {
            if (!baseComponent)
                continue;

            IEntity baseOwner = baseComponent.GetOwner();
            if (!baseOwner)
                continue;

            vector pos = baseOwner.GetOrigin();
            pos[1] = GetGame().GetWorld().GetSurfaceY(pos[0], pos[2]);

            string baseName = baseComponent.GetCallsignDisplayNameOnly();
            if (baseName == "" && baseOwner.GetPrefabData())
                baseName = baseOwner.GetPrefabData().GetPrefabName();
            if (baseName == "")
                baseName = "Military Base";

            GM_SpawnAnchor anchor = new GM_SpawnAnchor();
            anchor.anchor_id = "military_base_" + (outAnchors.Count() + 1);
            anchor.name = baseName;
            anchor.position = pos;
            anchor.anchor_type = "neutral_base";
            anchor.control_state = "neutral";
            anchor.tactical_radius = Math.Max(baseComponent.GetRadius(), 150.0);
            anchor.priority = 0.8;
            anchor.notes = "Auto-detected military base / capture zone. Good for staging, occupation, or reserve forces.";

            UpsertSpawnAnchor(anchor, outAnchors);
        }
    }

    protected void AppendPlayerTrailAnchors(array<ref GM_SpawnAnchor> outAnchors)
    {
        if (!outAnchors)
            return;

        for (int i = 0; i < m_mPlayerPositionTrails.Count(); i++)
        {
            int playerId = m_mPlayerPositionTrails.GetKey(i);
            ref array<vector> trail = m_mPlayerPositionTrails.GetElement(i);
            if (!trail || trail.Count() < 3)
                continue;

            vector projectedPos;
            vector routeDir;
            float travelDistance = PredictTrailProjection(trail, projectedPos, routeDir);
            if (travelDistance < 35.0)
                continue;

            GM_SpawnAnchor anchor = new GM_SpawnAnchor();
            anchor.anchor_id = "trail_anchor_" + playerId;
            anchor.name = "Projected Route P" + playerId;
            anchor.position = projectedPos;
            anchor.control_state = "unassigned";

            if (travelDistance >= 120.0)
            {
                anchor.anchor_type = "roadblock_candidate";
                anchor.tactical_radius = 95.0;
            }
            else
            {
                anchor.anchor_type = "road_junction";
                anchor.tactical_radius = 120.0;
            }

            float priority = 0.6 + Math.Min(travelDistance / 250.0, 0.35);
            if (priority > 0.95)
                priority = 0.95;
            anchor.priority = priority;
            anchor.notes = "Projected from recent player movement trail; likely route for a road ambush, checkpoint, or vehicle-denial action.";

            UpsertSpawnAnchor(anchor, outAnchors);
        }
    }

    protected float PredictTrailProjection(array<vector> trail, out vector projectedPos, out vector projectedDirection)
    {
        projectedPos = vector.Zero;
        projectedDirection = Vector(1, 0, 0);

        if (!trail || trail.Count() < 2)
            return 0;

        vector directionAccumulator = vector.Zero;
        float totalTravel = 0;
        int startIndex = Math.Max(1, trail.Count() - 3);

        for (int i = startIndex; i < trail.Count(); i++)
        {
            vector previous = trail[i - 1];
            vector current = trail[i];
            vector segment = current - previous;
            segment[1] = 0;

            float segmentLength = segment.Length();
            if (segmentLength <= 0.1)
                continue;

            totalTravel += segmentLength;
            directionAccumulator[0] = directionAccumulator[0] + (segment[0] / segmentLength);
            directionAccumulator[2] = directionAccumulator[2] + (segment[2] / segmentLength);
        }

        float dirLength = directionAccumulator.Length();
        if (dirLength <= 0.01)
            return totalTravel;

        projectedDirection = directionAccumulator / dirLength;

        float projectDistance = 100.0;
        if (totalTravel >= 80.0)
            projectDistance = 160.0;
        if (totalTravel >= 160.0)
            projectDistance = 220.0;

        vector latest = trail[trail.Count() - 1];
        projectedPos = OffsetPosition(latest, projectedDirection, projectDistance);
        return totalTravel;
    }

    protected bool UpsertSpawnAnchor(GM_SpawnAnchor anchor, array<ref GM_SpawnAnchor> outAnchors)
    {
        if (!anchor || !outAnchors)
            return false;

        for (int i = 0; i < outAnchors.Count(); i++)
        {
            GM_SpawnAnchor existing = outAnchors[i];
            if (!existing)
                continue;

            float dx = existing.position[0] - anchor.position[0];
            float dz = existing.position[2] - anchor.position[2];
            float dist = Math.Sqrt(dx * dx + dz * dz);

            if (existing.anchor_type == anchor.anchor_type && dist <= Math.Max(35.0, Math.Min(existing.tactical_radius, anchor.tactical_radius)))
            {
                if (anchor.priority > existing.priority)
                    existing.priority = anchor.priority;
                if (existing.notes == "" && anchor.notes != "")
                    existing.notes = anchor.notes;
                if (existing.name == "" && anchor.name != "")
                    existing.name = anchor.name;
                if (anchor.tactical_radius > existing.tactical_radius)
                    existing.tactical_radius = anchor.tactical_radius;
                return false;
            }
        }

        outAnchors.Insert(anchor);
        return true;
    }

    protected void FinalizeSpawnAnchors(array<ref GM_SpawnAnchor> anchors)
    {
        if (!anchors)
            return;

        for (int i = 0; i < anchors.Count(); i++)
        {
            GM_SpawnAnchor anchor = anchors[i];
            if (!anchor)
                continue;

            if (anchor.priority < 0.0)
                anchor.priority = 0.0;
            if (anchor.priority > 1.0)
                anchor.priority = 1.0;
            if (anchor.tactical_radius <= 0)
                anchor.tactical_radius = 100.0;
            if (anchor.anchor_id == "")
                anchor.anchor_id = anchor.anchor_type + "_" + (i + 1);
        }

        for (int i = 0; i < anchors.Count(); i++)
        {
            for (int j = i + 1; j < anchors.Count(); j++)
            {
                GM_SpawnAnchor left = anchors[i];
                GM_SpawnAnchor right = anchors[j];
                if (!left || !right)
                    continue;

                if (right.priority > left.priority)
                {
                    anchors[i] = right;
                    anchors[j] = left;
                }
            }
        }
    }

    protected string SanitizeAnchorType(string anchorType)
    {
        switch (anchorType)
        {
            case "neutral_base":
            case "road_junction":
            case "roadblock_candidate":
                return anchorType;
        }

        return "road_junction";
    }

    protected string SanitizeControlState(string controlState)
    {
        switch (controlState)
        {
            case "neutral":
            case "opfor":
            case "blufor":
            case "unassigned":
                return controlState;
        }

        return "unassigned";
    }

    protected void AppendConfiguredAnchors(string raw,
        string anchorType,
        string controlState,
        float defaultRadius,
        array<ref GM_SpawnAnchor> outAnchors)
    {
        if (!outAnchors) return;
        if (raw.Trim() == "") return;

        array<string> entries = {};
        raw.Split(";", entries, false);

        foreach (string entryRaw : entries)
        {
            string entry = entryRaw.Trim();
            if (entry == "")
                continue;

            array<string> parts = {};
            entry.Split("|", parts, false);
            if (parts.Count() < 3)
            {
                Print("[GodMode] Invalid spawn anchor entry '" + entry +
                      "' — expected Name|x|z|radius", LogLevel.WARNING);
                continue;
            }

            string name = parts[0].Trim();
            float x = parts[1].Trim().ToFloat();
            float z = parts[2].Trim().ToFloat();
            float radius = defaultRadius;
            if (parts.Count() >= 4 && parts[3].Trim() != "")
                radius = parts[3].Trim().ToFloat();
            if (radius <= 0)
                radius = defaultRadius;

            string notes = GetSpawnAnchorNotes(anchorType);
            if (parts.Count() >= 5 && parts[4].Trim() != "")
                notes = parts[4].Trim();

            vector pos = Vector(x, 0, z);
            pos[1] = GetGame().GetWorld().GetSurfaceY(pos[0], pos[2]);

            GM_SpawnAnchor anchor = new GM_SpawnAnchor();
            anchor.anchor_id = anchorType + "_" + (outAnchors.Count() + 1);
            anchor.name = name;
            anchor.position = pos;
            anchor.anchor_type = anchorType;
            anchor.control_state = controlState;
            anchor.tactical_radius = radius;

            float priority = 1.0 - (outAnchors.Count() * 0.05);
            if (priority < 0.25)
                priority = 0.25;
            anchor.priority = priority;
            anchor.notes = notes;

            UpsertSpawnAnchor(anchor, outAnchors);
        }
    }

    protected string GetSpawnAnchorNotes(string anchorType)
    {
        switch (anchorType)
        {
            case "neutral_base":
                return "Best for staging, occupation, or reinforcing uncaptured compounds.";
            case "road_junction":
                return "Best for ambushes, checkpoints, convoy interdiction, or screening a roadway.";
            case "roadblock_candidate":
                return "Best for roadblocks, mines, or blocking player vehicle movement.";
        }

        return "Configured map anchor.";
    }

    // ═══════════════════════════════════════════════
    // New Command Implementations
    // ═══════════════════════════════════════════════

    protected void ExecuteMortarStrike(GM_Command cmd)
    {
        vector center = cmd.GetTargetVector();
        // Parse parameters: {"rounds":6,"spread_radius":40,"delay_between_ms":1500,"type":"he"}
        int rounds = 6;
        float spreadRadius = 40;
        int delayMs = 1500;
        string strikeType = "he";

        // Simple parameter extraction (parameters is a JSON string)
        if (cmd.parameters.Contains("rounds"))
        {
            // Use basic string parsing for JSON values
            // In production, use a proper JSON parser
        }

        Print("[GodMode] Mortar strike: " + rounds + " rounds, " + spreadRadius + "m spread at " + center.ToString(false));

        for (int i = 0; i < rounds; i++)
        {
            // Schedule each round with delay
            float angle = Math.RandomFloat(0, Math.PI2);
            float dist = Math.RandomFloat(0, spreadRadius);
            vector impactPos = center;
            impactPos[0] = impactPos[0] + Math.Cos(angle) * dist;
            impactPos[2] = impactPos[2] + Math.Sin(angle) * dist;

            int totalDelay = i * delayMs;

            if (strikeType == "smoke")
                GetGame().GetCallqueue().CallLater(MortarSmokeImpact, totalDelay, false, impactPos);
            else
                GetGame().GetCallqueue().CallLater(MortarHEImpact, totalDelay, false, impactPos);
        }

        m_MissionEngine.RecordOpforSpawn(); // Track as fire mission
    }

    protected void MortarHEImpact(vector position)
    {
        m_ParticleCtrl.SpawnExplosion(position, "large");
        m_SoundCtrl.PlayThunderNear(position); // Explosion sound
    }

    protected void MortarSmokeImpact(vector position)
    {
        m_ParticleCtrl.SpawnSmoke(position, "white");
    }

    protected void ExecuteAirSupport(GM_Command cmd)
    {
        vector center = cmd.GetTargetVector();
        // Simulated gun/rocket run: explosions along a line
        float dirDeg = 270; // default west-to-east
        float runLength = 200;

        float dirRad = dirDeg * Math.DEG2RAD;
        vector start = center;
        start[0] = start[0] - Math.Cos(dirRad) * runLength * 0.5;
        start[2] = start[2] - Math.Sin(dirRad) * runLength * 0.5;

        int impacts = 10;
        float stepX = Math.Cos(dirRad) * runLength / impacts;
        float stepZ = Math.Sin(dirRad) * runLength / impacts;

        Print("[GodMode] Air support: gun run along " + runLength + "m at " + center.ToString(false));

        for (int i = 0; i < impacts; i++)
        {
            vector impactPos = start;
            impactPos[0] = impactPos[0] + stepX * i + Math.RandomFloat(-10, 10);
            impactPos[2] = impactPos[2] + stepZ * i + Math.RandomFloat(-10, 10);

            int delay = i * 200; // 200ms between impacts
            GetGame().GetCallqueue().CallLater(MortarHEImpact, delay, false, impactPos);
        }
    }

    protected void ExecuteSupplyDrop(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();
        // Mark with smoke and flare
        m_ParticleCtrl.SpawnSmoke(position, "green");
        m_ParticleCtrl.SpawnFlare(position);
        Print("[GodMode] Supply drop marked at " + position.ToString(false));

        // NOTE: In a full implementation, spawn a supply crate prefab here
        // ResourceName crateRes = GetSupplyCratePrefab(cmd.parameters);
        // Spawn crate entity at position
    }

    protected void ExecuteSpawnConvoy(GM_Command cmd)
    {
        vector startPos = cmd.GetTargetVector();
        // Parse parameters for vehicle list and destination
        // For now, spawn a default convoy pattern
        string faction = "ussr";

        // Spawn vehicles in a line with 30m spacing
        ref array<string> vehicleTypes = {"uaz469_mg", "ural_truck", "ural_truck", "uaz469_mg"};
        float spacing = 30;

        for (int i = 0; i < vehicleTypes.Count(); i++)
        {
            vector vehPos = startPos;
            vehPos[2] = vehPos[2] - (spacing * i); // Line up behind start position

            m_VehicleMgr.SpawnCrewedVehicle(vehicleTypes[i], vehPos, faction);
        }

        Print("[GodMode] Convoy spawned: " + vehicleTypes.Count() + " vehicles at " + startPos.ToString(false));
    }

    protected void ExecuteEstablishCheckpoint(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();
        string size = "medium"; // parse from cmd.parameters

        // Spawn defending infantry
        if (size == "small")
        {
            m_SquadMgr.SpawnGroup("ussr_fire_team", position);
        }
        else if (size == "medium")
        {
            string sid = m_SquadMgr.SpawnGroup("ussr_rifle_squad", position);
            if (sid != "")
                m_SquadMgr.OrderDefend(sid, position);

            // Add an MG-armed vehicle
            m_VehicleMgr.SpawnCrewedVehicle("uaz469_mg", position, "ussr");
        }
        else // large
        {
            string sid1 = m_SquadMgr.SpawnGroup("ussr_rifle_squad", position);
            string sid2 = m_SquadMgr.SpawnGroup("ussr_mg_team",
                Vector(position[0] + 20, 0, position[2]));
            if (sid1 != "") m_SquadMgr.OrderDefend(sid1, position);
            if (sid2 != "") m_SquadMgr.OrderDefend(sid2, position);

            m_VehicleMgr.SpawnCrewedVehicle("btr70", position, "ussr");
        }

        Print("[GodMode] Checkpoint established (" + size + ") at " + position.ToString(false));
    }

    protected void ExecuteLayMines(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();
        float radius = 30;
        int mineCount = 6;
        string mineType = "anti_personnel";

        // Parse density
        if (cmd.parameters.Contains("sparse"))
            mineCount = 3;
        else if (cmd.parameters.Contains("dense"))
            mineCount = 12;

        if (cmd.parameters.Contains("anti_vehicle"))
            mineType = "anti_vehicle";

        string minefieldId = m_MissionEngine.CreateMinefield(position, radius, mineCount, mineType);
        Print("[GodMode] Minefield laid: " + minefieldId);
    }

    protected void ExecuteSetRoadblock(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();
        vector routeDir = EstimateLikelyRouteDirection(position);
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

        m_VehicleMgr.SpawnVehicle(centerObstacle, position);
        m_VehicleMgr.SpawnVehicle(flankObstacle, OffsetPosition(position, lateral, 9.0));
        m_VehicleMgr.SpawnVehicle(flankObstacle, OffsetPosition(position, lateral, -9.0));

        if (cmd.parameters.Contains("deep") || cmd.parameters.Contains("checkpoint"))
        {
            m_VehicleMgr.SpawnVehicle("uaz469", OffsetPosition(position, routeDir, 10.0));
            m_VehicleMgr.SpawnVehicle("uaz469", OffsetPosition(position, routeDir, -10.0));
        }

        string squadId = m_SquadMgr.SpawnGroup(defenders, OffsetPosition(position, routeDir, 20.0));
        if (squadId != "")
            m_SquadMgr.OrderDefend(squadId, position);

        if (cmd.parameters.Contains("mine"))
            m_MissionEngine.CreateMinefield(position, 25, 4, "anti_vehicle");

        m_ParticleCtrl.SpawnSmoke(position, "white");
        m_ParticleCtrl.SpawnSmoke(OffsetPosition(position, lateral, 6.0), "white");

        Print("[GodMode] Physical roadblock set at " + position.ToString(false) +
              " using " + centerObstacle + " + " + flankObstacle + " obstacles");
    }

    protected vector EstimateLikelyRouteDirection(vector nearPosition)
    {
        vector bestDirection = Vector(1, 0, 0);
        float bestDistance = 999999.0;

        for (int i = 0; i < m_mPlayerPositionTrails.Count(); i++)
        {
            ref array<vector> trail = m_mPlayerPositionTrails.GetElement(i);
            if (!trail || trail.Count() < 2)
                continue;

            vector projectedPos;
            vector trailDirection;
            float travel = PredictTrailProjection(trail, projectedPos, trailDirection);
            if (travel <= 0.1)
                continue;

            vector latest = trail[trail.Count() - 1];
            float dx = latest[0] - nearPosition[0];
            float dz = latest[2] - nearPosition[2];
            float dist = Math.Sqrt(dx * dx + dz * dz);
            if (dist < bestDistance)
            {
                bestDistance = dist;
                bestDirection = trailDirection;
            }
        }

        if (bestDirection.Length() <= 0.01)
            return Vector(1, 0, 0);

        return bestDirection;
    }

    protected vector OffsetPosition(vector origin, vector direction, float distance)
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

    protected void ExecuteSpawnMarker(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();
        string color = "green"; // parse from parameters

        if (cmd.parameters.Contains("smoke_signal"))
        {
            m_ParticleCtrl.SpawnSmoke(position, color);
        }
        else if (cmd.parameters.Contains("strobe"))
        {
            m_ParticleCtrl.SpawnFlare(position);
        }
        else
        {
            // Default: smoke
            m_ParticleCtrl.SpawnSmoke(position, color);
        }

        Print("[GodMode] Marker placed at " + position.ToString(false));
    }

    protected void ExecuteStartScenario(GM_Command cmd)
    {
        // Parse: {"scenario":"morning_patrol","archetype":"patrol_ambush","curve":"standard_bell","duration_min":45}
        // Simplified parsing — in production, use proper JSON extraction
        string scenario = "custom";
        string archetype = "patrol_ambush";
        string curve = "standard_bell";
        float duration = 40;

        m_MissionEngine.StartScenario(scenario, archetype, curve, duration);
    }

    // ═══════════════════════════════════════════════
    // Flanking Maneuver (lateral offset then attack)
    // ═══════════════════════════════════════════════

    protected void ExecuteFlankManeuver(GM_Command cmd, int side)
    {
        // side: -1 = left, +1 = right
        SCR_AIGroup group = m_SquadMgr.GetGroupPublic(cmd.unit_id);
        if (!group)
        {
            Print("[GodMode] Flank: squad " + cmd.unit_id + " not found", LogLevel.WARNING);
            return;
        }

        vector groupPos = group.GetOrigin();
        vector target = cmd.GetTargetVector();
        vector toTarget = target - groupPos;
        float dist = toTarget.Length();
        if (dist < 1) dist = 1;

        // Normalize the approach direction
        vector forward = toTarget / dist;

        // Calculate perpendicular direction (left = -90 deg, right = +90 deg)
        // Perpendicular in XZ plane: rotate forward by 90 degrees
        vector perp = Vector(forward[2] * side, 0, -forward[0] * side);

        // Flanking waypoint: offset laterally by 100-150m, then advance to target depth
        float lateralOffset = 120.0;
        float advanceRatio = 0.6; // move 60% of the way toward target before turning in

        vector flankWaypoint = Vector(
            groupPos[0] + forward[0] * dist * advanceRatio + perp[0] * lateralOffset,
            0,
            groupPos[2] + forward[2] * dist * advanceRatio + perp[2] * lateralOffset
        );
        flankWaypoint[1] = GetGame().GetWorld().GetSurfaceY(flankWaypoint[0], flankWaypoint[2]);

        // Clear existing waypoints and issue the flanking route
        group.CompleteAllWaypoints();

        // Step 1: Move to the flanking position (lateral offset)
        m_SquadMgr.OrderMove(cmd.unit_id, flankWaypoint);

        // Step 2: After reaching flank position, attack the target
        // Use CallLater to add the attack waypoint after initial movement starts
        string attackSquadId = cmd.unit_id;
        vector attackTarget = target;
        GetGame().GetCallqueue().CallLater(DelayedFlankAttack, 3000, false, attackSquadId, attackTarget);

        string sideStr = "RIGHT";
        if (side < 0)
            sideStr = "LEFT";
        Print("[GodMode] Flank " + sideStr + ": " + cmd.unit_id
              + " via " + flankWaypoint.ToString(false)
              + " → " + target.ToString(false));
        LogEvent("flank_" + sideStr + ":" + cmd.unit_id);
    }

    protected void DelayedFlankAttack(string squadId, vector target)
    {
        m_SquadMgr.OrderAttack(squadId, target);
    }

    // ═══════════════════════════════════════════════
    // Coordinated Operation Handler
    // ═══════════════════════════════════════════════

    protected void ExecuteCoordinatedOperation(GM_Command cmd)
    {
        // Coordinated operations are compound commands that combine multiple
        // sub-orders. The parameters field contains a JSON description of
        // the sub-commands. For now, log and dispatch the primary unit action.
        Print("[GodMode] Coordinated operation: unit=" + cmd.unit_id +
              " | params=" + cmd.parameters);

        // Default behavior: move the primary unit to the target position
        // In a full implementation, parse sub-commands from parameters and
        // dispatch each one with sync_group coordination.
        if (cmd.unit_id != "" && cmd.unit_id != "new")
        {
            m_SquadMgr.OrderMove(cmd.unit_id, cmd.GetTargetVector());
        }

        LogEvent("coordinated_op:" + cmd.unit_id);
    }

    // ═══════════════════════════════════════════════
    // CAS Run (Close Air Support via AmbientBattlefield)
    // ═══════════════════════════════════════════════

    protected void ExecuteCASRun(GM_Command cmd)
    {
        vector target = cmd.GetTargetVector();

        // Parse approach bearing and type from parameters
        string strikeType = "gun_run";
        float bearing = 270; // default west-to-east
        int passes = 1;

        if (cmd.parameters.Contains("bomb"))
            strikeType = "bomb_run";
        if (cmd.parameters.Contains("rocket"))
            strikeType = "rocket_run";

        // Dispatch to AmbientBattlefield air strike system
        if (m_AmbientBattlefield)
        {
            m_AmbientBattlefield.ExecuteAirStrike(target, strikeType, bearing, passes);
        }

        Print("[GodMode] CAS run: " + strikeType + " at " + target.ToString(false));
        LogEvent("cas_run:" + strikeType);
    }

    // ═══════════════════════════════════════════════
    // Troop Insert (spawn infantry at target position)
    // ═══════════════════════════════════════════════

    protected void ExecuteTroopInsert(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();

        // Parse squad type from parameters, default to rifle squad
        string squadType = "ussr_rifle_squad";
        if (cmd.parameters != "" && cmd.parameters != "{}")
            squadType = cmd.parameters;

        // Simulate helicopter insertion: play heli sound then spawn troops
        if (m_SoundCtrl)
        {
            vector heliPos = position;
            heliPos[1] = heliPos[1] + 50;
            m_SoundCtrl.PlaySoundAt("SOUND_HELICOPTER_DISTANT", heliPos);
        }

        // Spawn the infantry after a short delay to simulate landing
        string sid = m_SquadMgr.SpawnGroup(squadType, position);
        if (sid != "")
        {
            m_SquadMgr.OrderDefend(sid, position);
        }

        Print("[GodMode] Troop insert: " + squadType + " at " + position.ToString(false));
        LogEvent("troop_insert:" + squadType);
    }

    // ═══════════════════════════════════════════════
    // Convoy Form (parse vehicle types and spawn convoy)
    // ═══════════════════════════════════════════════

    protected void ExecuteConvoyForm(GM_Command cmd)
    {
        vector startPos = cmd.GetTargetVector();

        // Parse vehicle types from parameters; default to standard convoy
        // Expected format: "uaz469_mg,ural_truck,btr70" or similar CSV
        ref array<string> vehicleTypes = {};
        if (cmd.parameters != "" && cmd.parameters.Contains(","))
        {
            cmd.parameters.Split(",", vehicleTypes, false);
        }
        else
        {
            // Default convoy composition
            vehicleTypes = {"uaz469_mg", "ural_truck", "ural_truck", "uaz469_mg"};
        }

        // Generate a convoy ID and use startPos as both start and destination
        // (convoy moves forward from spawn point; LLM can issue follow-up move commands)
        string convoyId = "convoy_" + m_iTotalCommandsExecuted;
        float spacing = 30;
        vector destination = startPos; // default: hold position; LLM sends move orders next
        m_VehicleMgr.SpawnConvoy(convoyId, vehicleTypes, startPos, destination, spacing);

        Print("[GodMode] Convoy formed: " + vehicleTypes.Count() + " vehicles at " + startPos.ToString(false));
        LogEvent("convoy_form:" + vehicleTypes.Count());
    }

    // ═══════════════════════════════════════════════
    // Vehicle Screen (defensive vehicle line)
    // ═══════════════════════════════════════════════

    protected void ExecuteVehicleScreen(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();

        // Order the specified vehicle to defend at the target position.
        // If the unit_id refers to a group of vehicles, order each one.
        // For now, dispatch a single vehicle defend order.
        m_VehicleMgr.OrderVehicleDefend(cmd.unit_id, position);

        Print("[GodMode] Vehicle screen: " + cmd.unit_id + " defending at " + position.ToString(false));
        LogEvent("vehicle_screen:" + cmd.unit_id);
    }

    // ═══════════════════════════════════════════════
    // Weather Progression (parse params and dispatch)
    // ═══════════════════════════════════════════════

    protected void ExecuteWeatherProgression(GM_Command cmd)
    {
        // Expected parameters: "overcast,30,true" or JSON with target_weather, duration_minutes, then_clear
        string targetWeather = "overcast";
        float totalMinutes = 30;
        bool thenClear = false;

        // Parse CSV parameters, guarding against JSON-object strings from LLM
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

        m_WeatherCtrl.StartWeatherProgression(targetWeather, totalMinutes, thenClear);

        Print("[GodMode] Weather progression: " + targetWeather + " over " + totalMinutes + "min (clear after: " + thenClear + ")");
    }

    // ═══════════════════════════════════════════════
    // Artillery Barrage (via AmbientBattlefield)
    // ═══════════════════════════════════════════════

    protected void ExecuteArtilleryBarrage(GM_Command cmd)
    {
        vector center = cmd.GetTargetVector();

        // Parse parameters: radius, rounds, duration, caliber
        float radius = 100;
        int rounds = 12;
        float duration = 30;
        string caliber = "standard";

        // Parse parameters: supports CSV "100,12,30,heavy" or JSON-like strings.
        // Guard against non-numeric values from LLM (e.g. JSON objects).
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

        // Fallback defaults for zero/invalid values
        if (radius <= 0) radius = 100;
        if (rounds <= 0) rounds = 12;
        if (duration <= 0) duration = 30;

        if (m_AmbientBattlefield)
            m_AmbientBattlefield.ExecuteArtilleryBarrage(center, radius, rounds, duration, caliber);

        Print("[GodMode] Artillery barrage dispatched: " + rounds + " rounds of " + caliber +
              " at radius " + radius + "m for " + duration + "s");
        LogEvent("artillery_barrage:" + center.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Building Fire (via AmbientBattlefield)
    // ═══════════════════════════════════════════════

    protected void ExecuteBuildingFire(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();

        // Parse parameters: intensity, duration_minutes
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

        if (m_AmbientBattlefield)
            m_AmbientBattlefield.StartBuildingFire(position, intensity, durationMinutes);

        Print("[GodMode] Building fire: " + intensity + " at " + position.ToString(false) + " for " + durationMinutes + "min");
        LogEvent("building_fire:" + position.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Distant Combat Ambience (via AmbientBattlefield)
    // ═══════════════════════════════════════════════

    protected void ExecuteDistantCombat(GM_Command cmd)
    {
        vector position = cmd.GetTargetVector();

        // Parse parameters: type, duration_seconds, intensity
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

        if (m_AmbientBattlefield)
            m_AmbientBattlefield.StartDistantCombat(position, combatType, durationSeconds, intensity);

        Print("[GodMode] Distant combat: " + combatType + " (" + intensity + ") at " +
              position.ToString(false) + " for " + durationSeconds + "s");
        LogEvent("distant_combat:" + combatType);
    }

    // ═══════════════════════════════════════════════
    // Public Accessors (for other systems)
    // ═══════════════════════════════════════════════

    GM_WeatherController GetWeatherController() { return m_WeatherCtrl; }
    GM_VehicleManager GetVehicleManager() { return m_VehicleMgr; }
    GM_SquadManager GetSquadManager() { return m_SquadMgr; }
    GM_NarrativeManager GetNarrativeManager() { return m_NarrativeMgr; }
    GM_TriggerManager GetTriggerManager() { return m_TriggerMgr; }
    GM_MissionEngine GetMissionEngine() { return m_MissionEngine; }
    GM_AmbientBattlefield GetAmbientBattlefield() { return m_AmbientBattlefield; }
    float GetMissionTime() { return m_fMissionTime; }
    int GetPlayerCount() { return m_iPlayerCount; }
    bool IsLLMEnabled() { return m_bLLMEnabled; }
}
