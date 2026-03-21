// GM_WeatherController.c — Weather, Time of Day, Wind, and Moon Phase control
// Wraps TimeAndWeatherManagerEntity with safe access patterns and dramatic sequences.

class GM_WeatherController
{
    // Track current state for SITREP reporting
    protected string m_sCurrentWeather = "Clear";
    protected float m_fWindSpeed = 0;
    protected float m_fWindDirection = 0;
    protected bool m_bWindOverrideActive = false;

    // ═══════════════════════════════════════════════
    // Manager Access (safe, null-checked)
    // ═══════════════════════════════════════════════

    protected TimeAndWeatherManagerEntity GetManager()
    {
        ChimeraWorld world = ChimeraWorld.CastFrom(GetGame().GetWorld());
        if (!world)
        {
            Print("[GodMode:Weather] ChimeraWorld not available", LogLevel.WARNING);
            return null;
        }
        return world.GetTimeAndWeatherManager();
    }

    // ═══════════════════════════════════════════════
    // Weather Control
    // ═══════════════════════════════════════════════

    // Available weather IDs:
    //   "Clear"    — blue sky, full visibility
    //   "Cloudy"   — overcast, reduced sun
    //   "Overcast" — heavy clouds, dim light
    //   "Rainy"    — rain, reduced visibility
    //   "Storm"    — heavy rain, thunder, low visibility
    //   "Foggy"    — thick fog, very low visibility

    void SetWeather(string weatherID, float transitionSeconds = 30.0,
                    float durationSeconds = 600.0, bool looping = true)
    {
        TimeAndWeatherManagerEntity mgr = GetManager();
        if (!mgr)
        {
            Print("[GodMode:Weather] Manager unavailable, cannot set weather", LogLevel.ERROR);
            return;
        }

        // Validate weather ID
        if (!IsValidWeatherID(weatherID))
        {
            Print("[GodMode:Weather] Invalid weather ID: " + weatherID, LogLevel.WARNING);
            return;
        }

        // Clamp transition time
        transitionSeconds = Math.Clamp(transitionSeconds, 1.0, 300.0);
        durationSeconds = Math.Clamp(durationSeconds, 10.0, 3600.0);

        mgr.ForceWeatherTo(looping, weatherID, transitionSeconds, durationSeconds);
        m_sCurrentWeather = weatherID;

        Print("[GodMode:Weather] → " + weatherID +
              " (transition: " + transitionSeconds + "s" +
              ", duration: " + durationSeconds + "s" +
              ", loop: " + looping + ")");
    }

    protected bool IsValidWeatherID(string id)
    {
        return (id == "Clear" || id == "Cloudy" || id == "Overcast" ||
                id == "Rainy" || id == "Storm" || id == "Foggy");
    }

    void LockWeather()
    {
        TimeAndWeatherManagerEntity mgr = GetManager();
        if (mgr) mgr.SetCurrentWeatherLooping(true);
    }

    void UnlockWeather()
    {
        TimeAndWeatherManagerEntity mgr = GetManager();
        if (mgr) mgr.SetCurrentWeatherLooping(false);
    }

    bool IsWeatherLooping()
    {
        TimeAndWeatherManagerEntity mgr = GetManager();
        if (!mgr) return false;
        return mgr.IsWeatherLooping();
    }

    string GetCurrentWeatherID()
    {
        return m_sCurrentWeather;
    }

    // ═══════════════════════════════════════════════
    // Dramatic Weather Sequences
    // ═══════════════════════════════════════════════

    // Storm buildup: clear → cloudy → overcast → storm
    void TriggerStormSequence()
    {
        SetWeather("Cloudy", 20.0, 60.0, false);

        GetGame().GetCallqueue().CallLater(
            SetWeather, 60000, false,
            "Overcast", 30.0, 90.0, false
        );

        GetGame().GetCallqueue().CallLater(
            SetWeather, 150000, false,
            "Storm", 20.0, 300.0, true
        );

        Print("[GodMode:Weather] Storm sequence initiated");
    }

    // Fog roll-in for ambush cover
    void TriggerFogRollIn(float transitionSeconds = 60.0)
    {
        SetWeather("Foggy", transitionSeconds, 300.0, true);
    }

    // Clear skies after storm
    void TriggerClearing(float transitionSeconds = 45.0)
    {
        SetWeather("Clear", transitionSeconds, 600.0, true);
    }

    // Rapid weather shift (for dramatic moments)
    void TriggerQuickWeatherShift(string targetWeather)
    {
        SetWeather(targetWeather, 10.0, 300.0, true);
    }

    // ═══════════════════════════════════════════════
    // Time of Day
    // ═══════════════════════════════════════════════

    float GetTimeOfDay()
    {
        TimeAndWeatherManagerEntity mgr = GetManager();
        if (!mgr) return 12.0;

        TimeContainer tc = mgr.GetTime();
        return tc.m_iHours + (tc.m_iMinutes / 60.0);
    }

    void SetTimeOfDay(float hours24)
    {
        TimeAndWeatherManagerEntity mgr = GetManager();
        if (!mgr)
        {
            Print("[GodMode:Weather] Manager unavailable, cannot set time", LogLevel.ERROR);
            return;
        }

        // Clamp to valid range
        hours24 = Math.Clamp(hours24, 0.0, 23.99);

        int hrs = Math.Floor(hours24);
        int mins = Math.Floor((hours24 - hrs) * 60);

        TimeContainer tc = new TimeContainer();
        tc.m_iHours = hrs;
        tc.m_iMinutes = mins;
        tc.m_iSeconds = 0;

        mgr.SetTime(tc);
        Print("[GodMode:Weather] Time → " + hrs + ":" + mins);
    }

    bool IsNightTime()
    {
        TimeAndWeatherManagerEntity mgr = GetManager();
        if (!mgr) return false;
        return mgr.IsSunSet();
    }

    bool IsDawn()
    {
        float t = GetTimeOfDay();
        return (t >= 5.0 && t <= 7.0);
    }

    bool IsDusk()
    {
        float t = GetTimeOfDay();
        return (t >= 18.0 && t <= 20.0);
    }

    // ═══════════════════════════════════════════════
    // Moon Phase
    // ═══════════════════════════════════════════════

    string GetMoonPhase()
    {
        TimeAndWeatherManagerEntity mgr = GetManager();
        if (!mgr) return "unknown";

        SCR_MoonPhaseUIInfo info = mgr.GetCurrentMoonPhaseInfoForDate();
        if (!info) return "unknown";
        return info.GetName();
    }

    // ═══════════════════════════════════════════════
    // Wind Control
    // ═══════════════════════════════════════════════

    // Wind speed reference:
    //   0–2   m/s  = Calm
    //   3–5   m/s  = Light breeze
    //   6–10  m/s  = Moderate wind
    //   11–15 m/s  = Strong wind (affects accuracy)
    //   16+   m/s  = Storm-force

    void SetWind(string params)
    {
        // Format: "speed,direction_degrees"
        // e.g., "8.5,270" = 8.5 m/s from the west
        array<string> parts = {};
        params.Split(",", parts);
        if (parts.Count() < 2)
        {
            Print("[GodMode:Weather] Invalid wind params: " + params, LogLevel.WARNING);
            return;
        }

        float speed = parts[0].Trim().ToFloat();
        float direction = parts[1].Trim().ToFloat();

        SetWindDirect(speed, direction);
    }

    void SetWindDirect(float speed, float direction)
    {
        TimeAndWeatherManagerEntity mgr = GetManager();
        if (!mgr) return;

        // Clamp values
        speed = Math.Clamp(speed, 0.0, 30.0);
        direction = Math.Clamp(direction, 0.0, 359.99);

        mgr.DelayedSetWindOverride(true);
        mgr.DelayedOverrideWindSpeed(speed);
        mgr.DelayedOverrideWindDirection(direction);

        m_fWindSpeed = speed;
        m_fWindDirection = direction;
        m_bWindOverrideActive = true;

        Print("[GodMode:Weather] Wind → " + speed + " m/s from " + direction + "°");
    }

    void ResetWindToNatural()
    {
        TimeAndWeatherManagerEntity mgr = GetManager();
        if (mgr)
        {
            mgr.DelayedSetWindOverride(false);
            m_bWindOverrideActive = false;
            Print("[GodMode:Weather] Wind override released");
        }
    }

    // Tactical: headwind opposing player movement
    void SetHeadwind(vector playerForward, float speed = 12.0)
    {
        float yaw = Math.Atan2(playerForward[0], playerForward[2]);
        float oppositeDeg = Math.RAD2DEG * yaw + 180.0;
        if (oppositeDeg >= 360) oppositeDeg -= 360;

        SetWindDirect(speed, oppositeDeg);
    }

    // Getters for SITREP
    float GetWindSpeed() { return m_fWindSpeed; }
    float GetWindDirection() { return m_fWindDirection; }
    bool IsWindOverrideActive() { return m_bWindOverrideActive; }
}
