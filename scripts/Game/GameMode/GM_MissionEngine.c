// GM_MissionEngine.c — Dynamic Mission Generation, Escalation Tracking, and Scenario Management
// Works alongside GM_GodModeComponent to provide structured mission flow.
// The LLM uses this state to make contextually appropriate decisions.

// ════════════════════════════════════════════════════════════
// MISSION STATE — Serialized into SITREP for LLM context
// ════════════════════════════════════════════════════════════

class GM_MissionState : JsonApiStruct
{
    string active_scenario_id;          // e.g. "morning_patrol", "convoy_gauntlet"
    string active_archetype;            // e.g. "patrol_ambush", "fob_defense"
    string escalation_curve;            // e.g. "standard_bell", "wave_assault"
    string current_phase;               // e.g. "lull", "contact", "climax"
    float phase_intensity;              // 0.0 - 1.0, current escalation intensity
    float mission_progress_pct;         // 0.0 - 1.0, overall mission progress
    string alert_level;                 // "green", "yellow", "orange", "red"
    int waves_completed;                // for wave-based missions
    int total_player_kills;             // cumulative player kills this mission
    int total_player_deaths;            // cumulative player deaths this mission
    int total_opfor_spawned;            // total OPFOR units spawned this mission
    int total_opfor_destroyed;          // total OPFOR units destroyed this mission
    float opfor_casualty_rate;          // 0.0 - 1.0
    float player_casualty_rate;         // 0.0 - 1.0
    string force_ratio_assessment;      // "opfor_dominant", "balanced", "player_dominant"
    ref array<string> completed_objectives;
    ref array<string> failed_objectives;
    ref array<string> active_minefields;    // minefield IDs for tracking
    string recommended_next_archetype;      // suggestion for LLM after current mission

    void GM_MissionState()
    {
        RegV("active_scenario_id");
        RegV("active_archetype");
        RegV("escalation_curve");
        RegV("current_phase");
        RegV("phase_intensity");
        RegV("mission_progress_pct");
        RegV("alert_level");
        RegV("waves_completed");
        RegV("total_player_kills");
        RegV("total_player_deaths");
        RegV("total_opfor_spawned");
        RegV("total_opfor_destroyed");
        RegV("opfor_casualty_rate");
        RegV("player_casualty_rate");
        RegV("force_ratio_assessment");
        RegV("completed_objectives");
        RegV("failed_objectives");
        RegV("active_minefields");
        RegV("recommended_next_archetype");
    }
}

// ════════════════════════════════════════════════════════════
// ESCALATION PHASE DEFINITION
// ════════════════════════════════════════════════════════════

class GM_EscalationPhase
{
    string name;
    float time_start_pct;       // 0.0 - 1.0 of mission duration
    float time_end_pct;
    float intensity;            // 0.0 - 1.0
    float spawn_rate_modifier;  // multiplier on normal spawn rate
    int max_simultaneous_contacts;

    void GM_EscalationPhase(string n, float start, float end, float intens, float spawnMod, int maxContacts)
    {
        name = n;
        time_start_pct = start;
        time_end_pct = end;
        intensity = intens;
        spawn_rate_modifier = spawnMod;
        max_simultaneous_contacts = maxContacts;
    }
}

// ════════════════════════════════════════════════════════════
// MISSION ENGINE
// ════════════════════════════════════════════════════════════

class GM_MissionEngine
{
    // ── State ──
    protected string m_sActiveScenarioId;
    protected string m_sActiveArchetype;
    protected string m_sEscalationCurve;
    protected string m_sAlertLevel;
    protected float m_fMissionStartTime;
    protected float m_fMissionTargetDuration;   // seconds
    protected int m_iWavesCompleted;

    // ── Tracking ──
    protected int m_iPlayerKills;
    protected int m_iPlayerDeaths;
    protected int m_iOpforSpawned;
    protected int m_iOpforDestroyed;
    protected ref array<string> m_aCompletedObjectives;
    protected ref array<string> m_aFailedObjectives;

    // ── Minefields ──
    protected ref map<string, ref array<SCR_BaseTriggerEntity>> m_mMinefields;
    protected int m_iMinefieldCounter;

    // ── Escalation curves ──
    protected ref array<ref GM_EscalationPhase> m_aCurrentCurve;

    // ── Reference ──
    protected GM_GodModeComponent m_pOwner;

    void GM_MissionEngine()
    {
        m_sAlertLevel = "green";
        m_fMissionTargetDuration = 2400; // 40 minutes default
        m_aCompletedObjectives = {};
        m_aFailedObjectives = {};
        m_mMinefields = new map<string, ref array<SCR_BaseTriggerEntity>>();
        m_iMinefieldCounter = 0;
    }

    void SetOwner(GM_GodModeComponent owner)
    {
        m_pOwner = owner;
    }

    // ═══════════════════════════════════════════════
    // Scenario Lifecycle
    // ═══════════════════════════════════════════════

    void StartScenario(string scenarioId, string archetype, string escalationCurve, float durationMinutes)
    {
        m_sActiveScenarioId = scenarioId;
        m_sActiveArchetype = archetype;
        m_sEscalationCurve = escalationCurve;
        m_fMissionTargetDuration = durationMinutes * 60;
        m_fMissionStartTime = m_pOwner.GetMissionTime();

        m_iWavesCompleted = 0;
        m_iPlayerKills = 0;
        m_iPlayerDeaths = 0;
        m_iOpforSpawned = 0;
        m_iOpforDestroyed = 0;
        m_aCompletedObjectives.Clear();
        m_aFailedObjectives.Clear();

        // Build escalation curve
        BuildEscalationCurve(escalationCurve);

        Print("[GodMode:Mission] Scenario started: " + scenarioId +
              " | Archetype: " + archetype +
              " | Curve: " + escalationCurve +
              " | Duration: " + durationMinutes + "min");

        if (m_pOwner)
            m_pOwner.LogEvent("scenario_started:" + scenarioId);
    }

    void EndScenario(string outcome)
    {
        Print("[GodMode:Mission] Scenario ended: " + m_sActiveScenarioId +
              " | Outcome: " + outcome);

        if (m_pOwner)
            m_pOwner.LogEvent("scenario_ended:" + m_sActiveScenarioId + ":outcome=" + outcome);

        m_sActiveScenarioId = "";
        m_sActiveArchetype = "";
    }

    // ═══════════════════════════════════════════════
    // Escalation Curve Management
    // ═══════════════════════════════════════════════

    protected void BuildEscalationCurve(string curveId)
    {
        m_aCurrentCurve = {};

        if (curveId == "standard_bell")
        {
            m_aCurrentCurve.Insert(new GM_EscalationPhase("lull",             0.00, 0.15, 0.1,  0.3, 0));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("rising_tension",   0.15, 0.35, 0.3,  0.6, 1));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("contact",          0.35, 0.55, 0.6,  1.0, 2));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("escalation",       0.55, 0.75, 0.85, 1.3, 3));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("climax",           0.75, 0.90, 1.0,  1.5, 4));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("resolution",       0.90, 1.00, 0.3,  0.2, 1));
        }
        else if (curveId == "slow_burn")
        {
            m_aCurrentCurve.Insert(new GM_EscalationPhase("quiet",       0.00, 0.40, 0.1,  0.2, 0));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("unease",      0.40, 0.60, 0.25, 0.4, 1));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("discovery",   0.60, 0.75, 0.5,  0.8, 2));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("eruption",    0.75, 0.90, 1.0,  1.5, 4));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("aftermath",   0.90, 1.00, 0.2,  0.2, 1));
        }
        else if (curveId == "wave_assault")
        {
            m_aCurrentCurve.Insert(new GM_EscalationPhase("calm",          0.00, 0.10, 0.0,  0.0, 0));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("wave_1",        0.10, 0.25, 0.4,  0.8, 2));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("lull_1",        0.25, 0.35, 0.1,  0.1, 0));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("wave_2",        0.35, 0.55, 0.7,  1.2, 3));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("lull_2",        0.55, 0.65, 0.15, 0.1, 0));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("wave_3_final",  0.65, 0.90, 1.0,  1.5, 4));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("resolution",    0.90, 1.00, 0.2,  0.2, 1));
        }
        else if (curveId == "ambush_spike")
        {
            m_aCurrentCurve.Insert(new GM_EscalationPhase("movement",       0.00, 0.55, 0.05, 0.1, 0));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("trigger",         0.55, 0.60, 1.0,  2.0, 4));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("firefight",       0.60, 0.80, 0.8,  1.2, 3));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("break_contact",   0.80, 1.00, 0.3,  0.3, 1));
        }
        else if (curveId == "rolling_thunder")
        {
            m_aCurrentCurve.Insert(new GM_EscalationPhase("initial_contact",  0.00, 0.15, 0.5, 0.8, 2));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("sustained_combat", 0.15, 0.50, 0.7, 1.0, 3));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("peak",             0.50, 0.75, 0.9, 1.3, 4));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("grinding",         0.75, 0.90, 0.6, 0.8, 2));
            m_aCurrentCurve.Insert(new GM_EscalationPhase("conclusion",       0.90, 1.00, 0.3, 0.3, 1));
        }
        else
        {
            // Default: flat moderate intensity
            m_aCurrentCurve.Insert(new GM_EscalationPhase("active", 0.0, 1.0, 0.5, 1.0, 2));
        }
    }

    GM_EscalationPhase GetCurrentPhase()
    {
        if (!m_aCurrentCurve || m_aCurrentCurve.Count() == 0) return null;

        float progress = GetMissionProgress();
        foreach (GM_EscalationPhase phase : m_aCurrentCurve)
        {
            if (progress >= phase.time_start_pct && progress < phase.time_end_pct)
                return phase;
        }

        // Past end — return last phase
        return m_aCurrentCurve[m_aCurrentCurve.Count() - 1];
    }

    float GetMissionProgress()
    {
        if (m_fMissionTargetDuration <= 0 || !m_pOwner) return 0;
        float elapsed = m_pOwner.GetMissionTime() - m_fMissionStartTime;
        return Math.Clamp(elapsed / m_fMissionTargetDuration, 0.0, 1.0);
    }

    // ═══════════════════════════════════════════════
    // Alert Level
    // ═══════════════════════════════════════════════

    void SetAlertLevel(string level)
    {
        if (level != "green" && level != "yellow" && level != "orange" && level != "red")
        {
            Print("[GodMode:Mission] Invalid alert level: " + level, LogLevel.WARNING);
            return;
        }

        string prev = m_sAlertLevel;
        m_sAlertLevel = level;
        Print("[GodMode:Mission] Alert level: " + prev + " → " + level);

        if (m_pOwner)
            m_pOwner.LogEvent("alert_level_changed:" + prev + ">" + level);
    }

    string GetAlertLevel() { return m_sAlertLevel; }

    // ═══════════════════════════════════════════════
    // Minefield Management
    // ═══════════════════════════════════════════════

    string CreateMinefield(vector center, float radius, int mineCount, string mineType)
    {
        if (!m_pOwner) return "";

        m_iMinefieldCounter++;
        string minefieldId = "mines_" + m_iMinefieldCounter;
        ref array<SCR_BaseTriggerEntity> mines = {};

        float triggerRadius = (mineType == "anti_vehicle") ? 5.0 : 3.0;
        string explosionSize = (mineType == "anti_vehicle") ? "large" : "small";

        GM_TriggerManager triggerMgr = m_pOwner.GetTriggerManager();
        if (!triggerMgr)
        {
            Print("[GodMode:Mission] No TriggerManager available for minefields", LogLevel.ERROR);
            return "";
        }

        for (int i = 0; i < mineCount; i++)
        {
            // Random position within radius
            float angle = Math.RandomFloat(0, Math.PI2);
            float dist = Math.RandomFloat(0, radius);
            vector minePos = center;
            minePos[0] = minePos[0] + Math.Cos(angle) * dist;
            minePos[2] = minePos[2] + Math.Sin(angle) * dist;
            minePos[1] = GetGame().GetWorld().GetSurfaceY(minePos[0], minePos[2]);

            string tag = "mine:" + minefieldId + ":" + i + ":" + explosionSize;
            SCR_BaseTriggerEntity trigger = triggerMgr.CreateTrigger(minePos, triggerRadius, tag);
            if (trigger)
                mines.Insert(trigger);
        }

        m_mMinefields.Set(minefieldId, mines);
        Print("[GodMode:Mission] Minefield created: " + minefieldId +
              " | " + mineCount + " " + mineType + " mines in " + radius + "m radius");

        if (m_pOwner)
            m_pOwner.LogEvent("minefield_created:" + minefieldId);

        return minefieldId;
    }

    void ClearMinefield(string minefieldId)
    {
        ref array<SCR_BaseTriggerEntity> mines;
        if (!m_mMinefields.Find(minefieldId, mines)) return;

        GM_TriggerManager triggerMgr = m_pOwner.GetTriggerManager();
        if (!triggerMgr) return;

        foreach (SCR_BaseTriggerEntity mine : mines)
        {
            if (mine)
                triggerMgr.RemoveTrigger(mine);
        }

        m_mMinefields.Remove(minefieldId);
        Print("[GodMode:Mission] Minefield cleared: " + minefieldId);
    }

    void ClearAllMinefields()
    {
        array<string> ids = {};
        for (int i = 0; i < m_mMinefields.Count(); i++)
            ids.Insert(m_mMinefields.GetKey(i));

        foreach (string id : ids)
            ClearMinefield(id);
    }

    // ═══════════════════════════════════════════════
    // Combat Tracking
    // ═══════════════════════════════════════════════

    void RecordPlayerKill()  { m_iPlayerKills++; }
    void RecordPlayerDeath() { m_iPlayerDeaths++; }
    void RecordOpforSpawn()  { m_iOpforSpawned++; }
    void RecordOpforKill()   { m_iOpforDestroyed++; }
    void RecordWaveComplete(){ m_iWavesCompleted++; }

    void RecordObjectiveComplete(string objId)
    {
        m_aCompletedObjectives.Insert(objId);
    }

    void RecordObjectiveFailed(string objId)
    {
        m_aFailedObjectives.Insert(objId);
    }

    // ═══════════════════════════════════════════════
    // Force Ratio Assessment
    // ═══════════════════════════════════════════════

    protected string AssessForceRatio()
    {
        if (m_iOpforSpawned <= 0) return "balanced";

        float opforSurvivalRate = 1.0;
        if (m_iOpforSpawned > 0)
            opforSurvivalRate = 1.0 - ((float)m_iOpforDestroyed / m_iOpforSpawned);

        float playerPressure = 0;
        if (m_iPlayerKills + m_iPlayerDeaths > 0)
            playerPressure = (float)m_iPlayerKills / (m_iPlayerKills + m_iPlayerDeaths);

        // Player dominance: high kill ratio + high OPFOR casualties
        if (playerPressure > 0.7 && opforSurvivalRate < 0.4)
            return "player_dominant";
        else if (playerPressure < 0.3 || opforSurvivalRate > 0.7)
            return "opfor_dominant";
        else
            return "balanced";
    }

    // ═══════════════════════════════════════════════
    // SITREP Data for LLM
    // ═══════════════════════════════════════════════

    ref GM_MissionState GetMissionState()
    {
        GM_MissionState state = new GM_MissionState();
        state.active_scenario_id = m_sActiveScenarioId;
        state.active_archetype = m_sActiveArchetype;
        state.escalation_curve = m_sEscalationCurve;
        state.alert_level = m_sAlertLevel;
        state.waves_completed = m_iWavesCompleted;
        state.mission_progress_pct = GetMissionProgress();
        state.total_player_kills = m_iPlayerKills;
        state.total_player_deaths = m_iPlayerDeaths;
        state.total_opfor_spawned = m_iOpforSpawned;
        state.total_opfor_destroyed = m_iOpforDestroyed;
        state.completed_objectives = m_aCompletedObjectives;
        state.failed_objectives = m_aFailedObjectives;
        state.force_ratio_assessment = AssessForceRatio();

        // Casualty rates
        if (m_iOpforSpawned > 0)
            state.opfor_casualty_rate = (float)m_iOpforDestroyed / m_iOpforSpawned;
        if (m_iPlayerKills + m_iPlayerDeaths > 0)
            state.player_casualty_rate = (float)m_iPlayerDeaths / (m_iPlayerKills + m_iPlayerDeaths);

        // Current phase from escalation curve
        GM_EscalationPhase phase = GetCurrentPhase();
        if (phase)
        {
            state.current_phase = phase.name;
            state.phase_intensity = phase.intensity;
        }
        else
        {
            state.current_phase = "unknown";
            state.phase_intensity = 0.5;
        }

        // Active minefields
        state.active_minefields = {};
        for (int i = 0; i < m_mMinefields.Count(); i++)
            state.active_minefields.Insert(m_mMinefields.GetKey(i));

        // Recommend next archetype based on outcome trends
        state.recommended_next_archetype = RecommendNextArchetype();

        return state;
    }

    protected string RecommendNextArchetype()
    {
        string ratio = AssessForceRatio();

        // If players are dominating, suggest harder missions
        if (ratio == "player_dominant")
        {
            if (m_sActiveArchetype == "patrol_ambush")
                return "search_destroy";
            if (m_sActiveArchetype == "fob_defense")
                return "counter_attack";
            if (m_sActiveArchetype == "search_destroy")
                return "meeting_engagement";
            return "counter_attack";
        }

        // If OPFOR is dominating, suggest easier follow-ons
        if (ratio == "opfor_dominant")
        {
            if (m_sActiveArchetype == "counter_attack")
                return "fob_defense";
            if (m_sActiveArchetype == "meeting_engagement")
                return "patrol_ambush";
            return "recon";
        }

        // Balanced: natural chain progression
        if (m_sActiveArchetype == "patrol_ambush")    return "search_destroy";
        if (m_sActiveArchetype == "convoy_escort")    return "fob_defense";
        if (m_sActiveArchetype == "fob_defense")      return "counter_attack";
        if (m_sActiveArchetype == "recon")            return "patrol_ambush";
        if (m_sActiveArchetype == "search_destroy")   return "convoy_escort";
        if (m_sActiveArchetype == "vip_extraction")   return "search_destroy";
        if (m_sActiveArchetype == "counter_attack")   return "meeting_engagement";
        if (m_sActiveArchetype == "meeting_engagement") return "fob_defense";

        return "patrol_ambush"; // safe default
    }

    // ═══════════════════════════════════════════════
    // Accessors
    // ═══════════════════════════════════════════════

    string GetActiveScenario() { return m_sActiveScenarioId; }
    string GetActiveArchetype() { return m_sActiveArchetype; }
    bool IsScenarioActive() { return m_sActiveScenarioId != ""; }
}
