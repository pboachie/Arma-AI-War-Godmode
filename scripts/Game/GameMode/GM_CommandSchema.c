// GM_CommandSchema.c — JSON data structures for LLM communication
// Defines all serializable types for SITREP (outgoing) and Commands (incoming).

// ════════════════════════════════════════════════════════════
// OUTGOING: Game state sent TO the LLM via SITREP
// ════════════════════════════════════════════════════════════

class GM_UnitState : JsonApiStruct
{
    string unit_id;
    string unit_type;       // "infantry_squad", "vehicle", "sniper_team", etc.
    vector position;
    int strength;           // number of alive members (1 for vehicles)
    string current_order;   // "patrol", "attack", "defend", "idle", "moving"
    float health_pct;       // 0.0 – 1.0
    string faction;         // "us", "ussr", "fia"

    // Order execution feedback (Priority 1)
    string order_status;        // "idle", "moving", "arrived", "engaged", "stuck", "wiped_out"
    float distance_to_objective; // meters to current waypoint target
    float time_since_order;     // seconds since last command was issued

    // Per-unit combat stats (Priority 6)
    int kills;                  // kills since last SITREP
    int casualties;             // losses since last SITREP
    string ammo_status;         // "full", "medium", "low", "empty"
    bool is_suppressed;         // currently taking effective fire
    float time_in_current_order; // seconds executing current order

    // Tactical posture fields
    string formation;           // current formation
    string combat_mode;         // current ROE
    string morale;              // "strong", "steady", "shaky", "broken"

    void GM_UnitState()
    {
        RegV("unit_id");
        RegV("unit_type");
        RegV("position");
        RegV("strength");
        RegV("current_order");
        RegV("health_pct");
        RegV("faction");
        RegV("order_status");
        RegV("distance_to_objective");
        RegV("time_since_order");
        RegV("kills");
        RegV("casualties");
        RegV("ammo_status");
        RegV("is_suppressed");
        RegV("time_in_current_order");
        RegV("formation");
        RegV("combat_mode");
        RegV("morale");
    }
}

class GM_ContactReport : JsonApiStruct
{
    string contact_id;
    vector last_known_position;
    string unit_type;
    float confidence;           // 0.0 – 1.0 (decays over time)
    float seconds_since_seen;

    void GM_ContactReport()
    {
        RegV("contact_id");
        RegV("last_known_position");
        RegV("unit_type");
        RegV("confidence");
        RegV("seconds_since_seen");
    }
}

class GM_PlayerState : JsonApiStruct
{
    int player_id;
    vector position;
    float health_pct;
    bool is_in_vehicle;
    string vehicle_type;

    // Movement vectors (Priority 4)
    vector velocity;            // direction + speed vector
    float heading;              // compass direction player is facing (0-360)
    ref array<vector> position_trail; // last 5 positions for path prediction

    void GM_PlayerState()
    {
        RegV("player_id");
        RegV("position");
        RegV("health_pct");
        RegV("is_in_vehicle");
        RegV("vehicle_type");
        RegV("velocity");
        RegV("heading");
        RegV("position_trail");
    }
}

class GM_TerrainSample : JsonApiStruct
{
    vector position;
    float elevation;            // meters above sea level
    string terrain_type;        // "forest", "open", "urban", "road", "water", "hill"
    float cover_density;        // 0.0-1.0 (building/tree density)

    void GM_TerrainSample()
    {
        RegV("position");
        RegV("elevation");
        RegV("terrain_type");
        RegV("cover_density");
    }
}

class GM_SpawnAnchor : JsonApiStruct
{
    string anchor_id;
    string name;
    vector position;
    string anchor_type;         // "neutral_base", "road_junction", "roadblock_candidate"
    string control_state;       // "neutral", "opfor", "blufor", "unassigned"
    float tactical_radius;      // recommended operating radius in meters
    float priority;             // 0.0-1.0 suggestion weight
    string notes;               // short human/LLM hint for how to use this anchor

    void GM_SpawnAnchor()
    {
        RegV("anchor_id");
        RegV("name");
        RegV("position");
        RegV("anchor_type");
        RegV("control_state");
        RegV("tactical_radius");
        RegV("priority");
        RegV("notes");
    }
}

class GM_WeatherTacticalContext : JsonApiStruct
{
    float visibility_range_meters;
    string audio_masking;
    string sniper_effectiveness;
    bool air_support_available;
    string glare_advantage_direction;
    string ambient_light_level;
    bool is_night;
    bool is_twilight;
    string moon_phase;
    float sun_bearing;

    void GM_WeatherTacticalContext()
    {
        RegV("visibility_range_meters");
        RegV("audio_masking");
        RegV("sniper_effectiveness");
        RegV("air_support_available");
        RegV("glare_advantage_direction");
        RegV("ambient_light_level");
        RegV("is_night");
        RegV("is_twilight");
        RegV("moon_phase");
        RegV("sun_bearing");
    }
}

// NOTE: GM_MissionState is defined in GM_MissionEngine.c (canonical definition)
// with extended fields for escalation tracking, casualty rates, and force assessment.
// Do not redefine it here to avoid duplicate class errors.

class GM_AmbientState : JsonApiStruct
{
    int ambient_intensity;          // 0-3
    int active_fires;
    int active_smoke_columns;
    bool artillery_active;
    string battlefield_noise_level; // "quiet", "sporadic", "moderate", "heavy"

    void GM_AmbientState()
    {
        RegV("ambient_intensity");
        RegV("active_fires");
        RegV("active_smoke_columns");
        RegV("artillery_active");
        RegV("battlefield_noise_level");
    }
}

class GM_GameState : JsonApiStruct
{
    ref array<ref GM_UnitState> friendly_units;
    ref array<ref GM_UnitState> enemy_units;
    ref array<ref GM_ContactReport> contacts;
    ref array<ref GM_PlayerState> players;
    string current_weather;
    bool weather_controls_available;
    float time_of_day;
    int player_count;
    ref array<string> recent_events;
    string current_objective;
    float mission_time_seconds;
    float wind_speed;
    float wind_direction;
    int total_spawned_groups;
    int total_spawned_vehicles;

    // Terrain context (Priority 5)
    ref array<ref GM_TerrainSample> terrain_samples;

    // Curated spawn / control anchors configured in Workbench
    ref array<ref GM_SpawnAnchor> spawn_anchors;

    // Mission engine state (for LLM mission-aware decisions)
    ref GM_MissionState mission_state;

    // Weather tactical overlay
    ref GM_WeatherTacticalContext weather_tactical_context;

    // Ambient battlefield state
    ref GM_AmbientState ambient_state;

    void GM_GameState()
    {
        RegV("friendly_units");
        RegV("enemy_units");
        RegV("contacts");
        RegV("players");
        RegV("current_weather");
        RegV("weather_controls_available");
        RegV("time_of_day");
        RegV("player_count");
        RegV("recent_events");
        RegV("current_objective");
        RegV("mission_time_seconds");
        RegV("wind_speed");
        RegV("wind_direction");
        RegV("total_spawned_groups");
        RegV("total_spawned_vehicles");
        RegV("terrain_samples");
        RegV("spawn_anchors");
        RegV("mission_state");
        RegV("weather_tactical_context");
        RegV("ambient_state");
    }
}

// ════════════════════════════════════════════════════════════
// INCOMING: Commands FROM the LLM
// ════════════════════════════════════════════════════════════

class GM_Command : JsonApiStruct
{
    string command_type;    // See GM_CommandTypes below
    string unit_id;         // Target unit ID or "new" for spawns
    string target_position; // "x,y,z" format
    string parameters;      // Command-specific params (JSON sub-object as string)
    string narrative;       // Flavor text shown to players
    string reasoning;       // LLM's tactical reasoning (debug/logging only)
    string formation;       // "line", "column", "wedge", "staggered_column", "echelon_left", "echelon_right"
    string combat_mode;     // "hold_fire", "fire_at_will", "return_fire"
    string speed_mode;      // "walk", "run", "sprint"
    string stance;          // "stand", "crouch", "prone"
    string sync_group;      // Coordination group ID for multi-squad operations

    void GM_Command()
    {
        RegV("command_type");
        RegV("unit_id");
        RegV("target_position");
        RegV("parameters");
        RegV("narrative");
        RegV("reasoning");
        RegV("formation");
        RegV("combat_mode");
        RegV("speed_mode");
        RegV("stance");
        RegV("sync_group");
    }

    protected bool HasUsableTargetPosition()
    {
        if (target_position == "") return false;

        array<string> parts = {};
        target_position.Split(",", parts, false);
        if (parts.Count() < 3) return false;

        for (int i = 0; i < 3; i++)
        {
            if (parts[i].Trim() == "")
                return false;
        }

        return true;
    }

    // Parse "123.4,56.7,890.1" → vector
    vector GetTargetVector()
    {
        if (!HasUsableTargetPosition()) return vector.Zero;

        array<string> parts = {};
        target_position.Split(",", parts, false);

        return Vector(
            parts[0].Trim().ToFloat(),
            parts[1].Trim().ToFloat(),
            parts[2].Trim().ToFloat()
        );
    }

    // Validate that this command has required fields
    bool IsValid()
    {
        if (command_type == "") return false;

        // Spawn commands need parameters + target_position
        if (command_type == GM_CommandTypes.SPAWN_INFANTRY ||
            command_type == GM_CommandTypes.SPAWN_VEHICLE ||
            command_type == GM_CommandTypes.SPAWN_REINFORCEMENTS)
        {
            if (parameters == "") return false;
            if (!HasUsableTargetPosition()) return false;
        }

        // Movement commands need unit_id + target_position
        if (command_type == GM_CommandTypes.MOVE ||
            command_type == GM_CommandTypes.ATTACK ||
            command_type == GM_CommandTypes.DEFEND ||
            command_type == GM_CommandTypes.PATROL ||
            command_type == GM_CommandTypes.RETREAT ||
            command_type == GM_CommandTypes.SET_AMBUSH ||
            command_type == GM_CommandTypes.SEEK_AND_DESTROY)
        {
            if (unit_id == "") return false;
            if (!HasUsableTargetPosition()) return false;
        }

        // Advanced tactical commands need unit_id + target_position
        if (command_type == GM_CommandTypes.SUPPRESS ||
            command_type == GM_CommandTypes.OVERWATCH ||
            command_type == GM_CommandTypes.BREACH ||
            command_type == GM_CommandTypes.FLANK_LEFT ||
            command_type == GM_CommandTypes.FLANK_RIGHT ||
            command_type == GM_CommandTypes.GARRISON ||
            command_type == GM_CommandTypes.SET_KEYHOLE ||
            command_type == GM_CommandTypes.ESTABLISH_OP ||
            command_type == GM_CommandTypes.SILENT_MOVE ||
            command_type == GM_CommandTypes.BOUNDING_OVERWATCH ||
            command_type == GM_CommandTypes.COORDINATED_OP)
        {
            if (unit_id == "") return false;
            if (!HasUsableTargetPosition()) return false;
        }

        // Fighting withdrawal needs unit_id but uses parameters, not target_position
        if (command_type == GM_CommandTypes.FIGHTING_WITHDRAWAL)
        {
            if (unit_id == "") return false;
        }

        // Patrol variants need unit_id + target_position
        if (command_type == GM_CommandTypes.PATROL_CLOVERLEAF ||
            command_type == GM_CommandTypes.PATROL_ZIGZAG)
        {
            if (unit_id == "") return false;
            if (!HasUsableTargetPosition()) return false;
        }

        // Vehicle tactical commands need unit_id + target_position
        if (command_type == GM_CommandTypes.HULL_DOWN ||
            command_type == GM_CommandTypes.OVERWATCH_POSITION ||
            command_type == GM_CommandTypes.DISMOUNT_ASSAULT ||
            command_type == GM_CommandTypes.CAS_RUN ||
            command_type == GM_CommandTypes.TROOP_INSERT ||
            command_type == GM_CommandTypes.CONVOY_FORM ||
            command_type == GM_CommandTypes.VEHICLE_SCREEN ||
            command_type == GM_CommandTypes.AIR_PATROL)
        {
            if (unit_id == "") return false;
            if (!HasUsableTargetPosition()) return false;
        }

        // Effects need target_position
        if (command_type == GM_CommandTypes.EXPLOSION ||
            command_type == GM_CommandTypes.SMOKE ||
            command_type == GM_CommandTypes.FLARE)
        {
            if (!HasUsableTargetPosition()) return false;
        }

        // Fire support needs target_position
        if (command_type == GM_CommandTypes.MORTAR_STRIKE ||
            command_type == GM_CommandTypes.AIR_SUPPORT)
        {
            if (!HasUsableTargetPosition()) return false;
        }

        // Logistics need target_position
        if (command_type == GM_CommandTypes.SUPPLY_DROP ||
            command_type == GM_CommandTypes.SPAWN_CONVOY)
        {
            if (!HasUsableTargetPosition()) return false;
        }

        // Artillery / advanced environment effects need target_position
        if (command_type == GM_CommandTypes.ARTILLERY_BARRAGE ||
            command_type == GM_CommandTypes.BUILDING_FIRE ||
            command_type == GM_CommandTypes.DISTANT_COMBAT)
        {
            if (!HasUsableTargetPosition()) return false;
        }

        // Cleanup area needs target_position (cleanup_dead and despawn_unit do not)
        if (command_type == GM_CommandTypes.CLEANUP_AREA)
        {
            if (!HasUsableTargetPosition()) return false;
        }

        // Despawn needs unit_id
        if (command_type == GM_CommandTypes.DESPAWN_UNIT)
        {
            if (unit_id == "") return false;
        }

        // Objective create needs target_position for marker placement
        if (command_type == GM_CommandTypes.OBJECTIVE_CREATE)
        {
            if (!HasUsableTargetPosition()) return false;
        }

        return true;
    }
}

class GM_CommandBatch : JsonApiStruct
{
    ref array<ref GM_Command> commands;
    string narrative_broadcast;     // Global message to all players
    string weather_command;         // Weather state ID ("" = no change)
    float time_override;            // -1 = no change, 0.0–24.0 = set time

    void GM_CommandBatch()
    {
        commands = {};
        narrative_broadcast = "";
        weather_command = "";
        time_override = -1;

        RegV("commands");
        RegV("narrative_broadcast");
        RegV("weather_command");
        RegV("time_override");
    }
}

// ════════════════════════════════════════════════════════════
// COMMAND TYPE CONSTANTS
// ════════════════════════════════════════════════════════════

class GM_CommandTypes
{
    // Movement & Combat
    static const string MOVE               = "move";
    static const string ATTACK             = "attack";
    static const string DEFEND             = "defend";
    static const string PATROL             = "patrol";
    static const string SET_AMBUSH         = "set_ambush";
    static const string SEEK_AND_DESTROY   = "seek_and_destroy";
    static const string RETREAT            = "retreat";

    // Spawning
    static const string SPAWN_INFANTRY     = "spawn_infantry";
    static const string SPAWN_VEHICLE      = "spawn_vehicle";
    static const string SPAWN_REINFORCEMENTS = "spawn_reinforcements";

    // Environment
    static const string SET_WEATHER        = "set_weather";
    static const string SET_TIME           = "set_time";
    static const string SET_WIND           = "set_wind";
    static const string THUNDER_NEAR       = "thunder_near";
    static const string PLAY_SOUND         = "play_sound";

    // Effects
    static const string EXPLOSION          = "explosion";
    static const string SMOKE              = "smoke";
    static const string FLARE              = "flare";

    // Cleanup
    static const string CLEANUP_AREA       = "cleanup_area";
    static const string CLEANUP_DEAD       = "cleanup_dead";
    static const string DESPAWN_UNIT       = "despawn_unit";

    // Narrative
    static const string RADIO_MESSAGE      = "radio_message";
    static const string OBJECTIVE_CREATE   = "objective_create";
    static const string OBJECTIVE_COMPLETE = "objective_complete";
    static const string OBJECTIVE_FAIL     = "objective_fail";

    // Fire Support
    static const string MORTAR_STRIKE      = "mortar_strike";
    static const string AIR_SUPPORT        = "air_support";

    // Logistics
    static const string SUPPLY_DROP        = "supply_drop";
    static const string SPAWN_CONVOY       = "spawn_convoy";

    // Fortification
    static const string ESTABLISH_CHECKPOINT = "establish_checkpoint";
    static const string LAY_MINES          = "lay_mines";
    static const string SET_ROADBLOCK      = "set_roadblock";

    // Markers
    static const string SPAWN_MARKER       = "spawn_marker";

    // Meta / Mission Control
    static const string SET_ALERT_LEVEL    = "set_alert_level";
    static const string ORDER_FORMATION    = "order_formation";
    static const string START_SCENARIO     = "start_scenario";
    static const string END_SCENARIO       = "end_scenario";

    // Advanced Tactical
    static const string SUPPRESS            = "suppress";
    static const string OVERWATCH           = "overwatch";
    static const string BREACH              = "breach";
    static const string FLANK_LEFT          = "flank_left";
    static const string FLANK_RIGHT         = "flank_right";
    static const string GARRISON            = "garrison";
    static const string SET_KEYHOLE         = "set_keyhole";
    static const string ESTABLISH_OP        = "establish_op";
    static const string FIGHTING_WITHDRAWAL = "fighting_withdrawal";
    static const string SILENT_MOVE         = "silent_move";

    // Patrol Variants
    static const string PATROL_CLOVERLEAF   = "patrol_cloverleaf";
    static const string PATROL_ZIGZAG       = "patrol_zigzag";

    // Coordinated Operations
    static const string BOUNDING_OVERWATCH  = "bounding_overwatch";
    static const string COORDINATED_OP      = "coordinated_operation";

    // Vehicle Tactical
    static const string HULL_DOWN           = "hull_down";
    static const string OVERWATCH_POSITION  = "overwatch_position";
    static const string DISMOUNT_ASSAULT    = "dismount_assault";
    static const string CAS_RUN             = "cas_run";
    static const string TROOP_INSERT        = "troop_insert";
    static const string CONVOY_FORM         = "convoy_form";
    static const string VEHICLE_SCREEN      = "vehicle_screen";
    static const string AIR_PATROL          = "air_patrol";

    // Advanced Environment
    static const string SET_WEATHER_PROGRESSION = "set_weather_progression";
    static const string SET_TACTICAL_LIGHTING   = "set_tactical_lighting";
    static const string SET_AMBIENT_INTENSITY   = "set_ambient_intensity";
    static const string ARTILLERY_BARRAGE       = "artillery_barrage";
    static const string BUILDING_FIRE           = "building_fire";
    static const string DISTANT_COMBAT          = "distant_combat_ambience";

    // Narrative Advanced
    static const string RADIO_COMMS         = "radio_comms";
    static const string INTEL_FEED          = "intel_feed";
    static const string FACTION_COMMS       = "faction_comms";
    static const string INTERCEPTED_COMMS   = "intercepted_comms";
    static const string PLAYER_ACK          = "player_acknowledgment";
    static const string PACING_DIRECTIVE    = "pacing_directive";
    static const string ENVIRONMENT_MOOD    = "environment_mood";
}
