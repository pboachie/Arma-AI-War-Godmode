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

        // Tick the ambient battlefield system
        if (m_AmbientBattlefield)
        {
            // Use average player position as center for ambient effects
            vector playerCenter = GetAveragePlayerPosition();
            m_AmbientBattlefield.Tick(timeSlice, playerCenter);
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

        // Clean up player tracking data to prevent memory leaks
        m_mPlayerPositionTrails.Remove(playerId);
        m_mPlayerLastPositions.Remove(playerId);
        m_mPlayerLastUpdateTimes.Remove(playerId);

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
                m_SquadMgr.OrderPatrolZigzag(cmd.unit_id, cmd.GetTargetVector(), cmd.GetTargetVector(), 80); break;

            // ── Compound Tactical (mapped to existing primitives) ──
            case GM_CommandTypes.FLANK_LEFT:
                Print("[GodMode] Flanking movement (left) — dispatching as move order");
                m_SquadMgr.OrderMove(cmd.unit_id, cmd.GetTargetVector()); break;
            case GM_CommandTypes.FLANK_RIGHT:
                Print("[GodMode] Flanking movement (right) — dispatching as move order");
                m_SquadMgr.OrderMove(cmd.unit_id, cmd.GetTargetVector()); break;
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
                Print("[GodMode] Air patrol requested — future implementation (logged only)"); break;

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
                m_WeatherCtrl.SetTimeOfDay(cmd.parameters.ToFloat()); break;
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
                m_AmbientBattlefield.SetIntensity(cmd.parameters.ToInt()); break;
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
        ref array<ref GM_PlayerState> players,
        ref array<ref GM_UnitState> enemyUnits)
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
        // Spawn disabled/wrecked vehicle as obstacle
        // For now, spawn smoke to visually mark the roadblock location
        m_ParticleCtrl.SpawnSmoke(position, "white");

        // NOTE: Full implementation would spawn barrier prefabs
        // ResourceName barrierRes = "{...}Prefabs/Structures/Military/Barriers/...";

        Print("[GodMode] Roadblock set at " + position.ToString(false));
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
            cmd.parameters.Split(",", vehicleTypes);
        }
        else
        {
            // Default convoy composition
            vehicleTypes = {"uaz469_mg", "ural_truck", "ural_truck", "uaz469_mg"};
        }

        m_VehicleMgr.SpawnConvoy(vehicleTypes, startPos);

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

        // Simple CSV parse
        ref array<string> parts = {};
        cmd.parameters.Split(",", parts);
        if (parts.Count() >= 1)
            targetWeather = parts[0].Trim();
        if (parts.Count() >= 2)
            totalMinutes = parts[1].Trim().ToFloat();
        if (parts.Count() >= 3)
            thenClear = (parts[2].Trim() == "true" || parts[2].Trim() == "1");

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

        // Simple CSV parse from parameters: "100,12,30,heavy"
        ref array<string> parts = {};
        cmd.parameters.Split(",", parts);
        if (parts.Count() >= 1)
            radius = parts[0].Trim().ToFloat();
        if (parts.Count() >= 2)
            rounds = parts[1].Trim().ToInt();
        if (parts.Count() >= 3)
            duration = parts[2].Trim().ToFloat();
        if (parts.Count() >= 4)
            caliber = parts[3].Trim();

        // Fallback defaults for zero/invalid values
        if (radius <= 0) radius = 100;
        if (rounds <= 0) rounds = 12;
        if (duration <= 0) duration = 30;

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

        ref array<string> parts = {};
        cmd.parameters.Split(",", parts);
        if (parts.Count() >= 1)
            intensity = parts[0].Trim();
        if (parts.Count() >= 2)
            durationMinutes = parts[1].Trim().ToFloat();

        if (durationMinutes <= 0) durationMinutes = 10;

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
        cmd.parameters.Split(",", parts);
        if (parts.Count() >= 1)
            combatType = parts[0].Trim();
        if (parts.Count() >= 2)
            durationSeconds = parts[1].Trim().ToFloat();
        if (parts.Count() >= 3)
            intensity = parts[2].Trim();

        if (durationSeconds <= 0) durationSeconds = 60;

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
