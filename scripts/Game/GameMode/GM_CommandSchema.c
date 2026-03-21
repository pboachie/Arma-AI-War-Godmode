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

class GM_GameState : JsonApiStruct
{
    ref array<ref GM_UnitState> friendly_units;
    ref array<ref GM_UnitState> enemy_units;
    ref array<ref GM_ContactReport> contacts;
    ref array<ref GM_PlayerState> players;
    string current_weather;
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

    void GM_GameState()
    {
        RegV("friendly_units");
        RegV("enemy_units");
        RegV("contacts");
        RegV("players");
        RegV("current_weather");
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

    void GM_Command()
    {
        RegV("command_type");
        RegV("unit_id");
        RegV("target_position");
        RegV("parameters");
        RegV("narrative");
        RegV("reasoning");
    }

    // Parse "123.4,56.7,890.1" → vector
    vector GetTargetVector()
    {
        if (target_position == "") return vector.Zero;

        array<string> parts = {};
        target_position.Split(",", parts);
        if (parts.Count() < 3) return vector.Zero;

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

        // Spawn commands need parameters
        if (command_type == "spawn_infantry" || command_type == "spawn_vehicle" || command_type == "spawn_reinforcements")
        {
            if (parameters == "") return false;
            if (target_position == "") return false;
        }

        // Movement commands need a target
        if (command_type == "move" || command_type == "attack" || command_type == "defend" ||
            command_type == "patrol" || command_type == "retreat")
        {
            if (unit_id == "") return false;
            if (target_position == "") return false;
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
}
