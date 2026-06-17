// GM_WeatherController.c — Weather, Time of Day, Wind, and Moon Phase control
// Wraps TimeAndWeatherManagerEntity with safe access patterns and dramatic sequences.

class GM_WeatherController
{
    // Track current state for SITREP reporting
    protected string m_sCurrentWeather = "Clear";
    protected float m_fWindSpeed = 0;
    protected float m_fWindDirection = 0;
    protected bool m_bWindOverrideActive = false;
    protected bool m_bMissingManagerWarned = false;

    // ═══════════════════════════════════════════════
    // Manager Access (safe, null-checked)
    // ═══════════════════════════════════════════════

    protected TimeAndWeatherManagerEntity GetManager(bool warnIfMissing = true)
    {
        ChimeraWorld world = ChimeraWorld.CastFrom(GetGame().GetWorld());
        if (!world)
        {
            if (warnIfMissing && !m_bMissingManagerWarned)
            {
                Print("[GodMode:Weather] ChimeraWorld not available yet; weather controls are disabled for now", LogLevel.WARNING);
                m_bMissingManagerWarned = true;
            }
            return null;
        }

        TimeAndWeatherManagerEntity mgr = world.GetTimeAndWeatherManager();
        if (!mgr)
        {
            if (warnIfMissing && !m_bMissingManagerWarned)
            {
                Print("[GodMode:Weather] TimeAndWeatherManagerEntity not present in this world; set_weather/set_time/set_wind will be skipped", LogLevel.WARNING);
                m_bMissingManagerWarned = true;
            }
            return null;
        }

        m_bMissingManagerWarned = false;
        return mgr;
    }

    bool HasManager()
    {
        return GetManager(false) != null;
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
            return;

        // Normalize LLM weather names to Enfusion IDs (e.g. "overcast" → "Overcast", "heavy_rain" → "Rainy")
        string normalized = NormalizeWeatherID(weatherID);
        if (normalized != "")
            weatherID = normalized;

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

    // Normalize LLM weather strings to Enfusion's expected case-sensitive IDs
    protected string NormalizeWeatherID(string id)
    {
        string lower = id;
        lower.ToLower();

        if (lower == "clear" || lower == "sunny")          return "Clear";
        if (lower == "cloudy" || lower == "clouds")        return "Cloudy";
        if (lower == "overcast")                           return "Overcast";
        if (lower == "rainy" || lower == "rain" ||
            lower == "light_rain" || lower == "heavy_rain") return "Rainy";
        if (lower == "storm" || lower == "thunderstorm" ||
            lower == "heavy_storm")                        return "Storm";
        if (lower == "foggy" || lower == "fog" ||
            lower == "mist" || lower == "haze")            return "Foggy";

        return "";  // unrecognized
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
            return;

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
        params.Split(",", parts, false);
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

    // ═══════════════════════════════════════════════
    // Weather Progression System
    // ═══════════════════════════════════════════════

    protected ref array<string> m_aWeatherQueue;
    protected int m_iQueueIndex;
    protected float m_fProgressionStepTimer;
    protected float m_fProgressionStepDuration;
    protected bool m_bProgressionActive;

    // Ordered severity scale for computing intermediate states
    static const ref array<string> SEVERITY_ORDER = {"Clear", "Cloudy", "Overcast", "Rainy", "Storm"};

    void StartWeatherProgression(string targetWeather, float totalMinutes, bool thenClear)
    {
        m_aWeatherQueue = {};
        int fromIdx = FindSeverityIndex(m_sCurrentWeather);
        int toIdx = FindSeverityIndex(targetWeather);

        // Handle fog as a branch off the main severity scale
        if (targetWeather == "Foggy")
        {
            if (fromIdx < 1) m_aWeatherQueue.Insert("Cloudy");
            if (fromIdx < 2) m_aWeatherQueue.Insert("Overcast");
            m_aWeatherQueue.Insert("Foggy");
        }
        else if (fromIdx >= 0 && toIdx >= 0)
        {
            int step = -1;
            if (toIdx > fromIdx)
                step = 1;
            for (int i = fromIdx + step; i >= 0 && i < SEVERITY_ORDER.Count(); i += step)
            {
                m_aWeatherQueue.Insert(SEVERITY_ORDER[i]);
                if (i == toIdx) break;
            }
        }
        else
        {
            m_aWeatherQueue.Insert(targetWeather);
        }

        // If thenClear, append the return path
        if (thenClear && targetWeather != "Clear")
        {
            int clearIdx = FindSeverityIndex("Clear");
            int currentEnd = FindSeverityIndex(targetWeather);
            if (currentEnd > 0 && clearIdx >= 0)
            {
                for (int j = currentEnd - 1; j >= clearIdx; j--)
                    m_aWeatherQueue.Insert(SEVERITY_ORDER[j]);
            }
        }

        if (m_aWeatherQueue.Count() == 0) return;

        m_fProgressionStepDuration = (totalMinutes * 60) / m_aWeatherQueue.Count();
        m_iQueueIndex = 0;
        m_fProgressionStepTimer = 0;
        m_bProgressionActive = true;

        Print("[GodMode:Weather] Progression started → " + targetWeather
              + " over " + totalMinutes + "min (" + m_aWeatherQueue.Count() + " steps)");
    }

    // Call this from EOnFrame
    void TickProgression(float deltaTime)
    {
        if (!m_bProgressionActive) return;
        m_fProgressionStepTimer += deltaTime;

        if (m_fProgressionStepTimer >= m_fProgressionStepDuration && m_iQueueIndex < m_aWeatherQueue.Count())
        {
            string next = m_aWeatherQueue[m_iQueueIndex];
            SetWeather(next, m_fProgressionStepDuration * 0.8, m_fProgressionStepDuration, false);
            m_iQueueIndex++;
            m_fProgressionStepTimer = 0;

            Print("[GodMode:Weather] Progression step " + m_iQueueIndex + "/" + m_aWeatherQueue.Count() + " → " + next);

            if (m_iQueueIndex >= m_aWeatherQueue.Count())
            {
                m_bProgressionActive = false;
                Print("[GodMode:Weather] Progression complete");
            }
        }
    }

    bool IsProgressionActive() { return m_bProgressionActive; }

    protected int FindSeverityIndex(string weather)
    {
        for (int i = 0; i < SEVERITY_ORDER.Count(); i++)
        {
            if (SEVERITY_ORDER[i] == weather) return i;
        }
        return -1; // Foggy or unknown
    }

    // ═══════════════════════════════════════════════
    // Tactical Weather Context (for SITREP enrichment)
    // ═══════════════════════════════════════════════

    float GetVisibilityRange()
    {
        switch (m_sCurrentWeather)
        {
            case "Clear":    return 2000;
            case "Cloudy":   return 1500;
            case "Overcast": return 1000;
            case "Rainy":    return 500;
            case "Storm":    return 300;
            case "Foggy":    return 100;
        }
        return 2000;
    }

    string GetAudioMasking()
    {
        switch (m_sCurrentWeather)
        {
            case "Storm":  return "high";
            case "Rainy":  return "moderate";
            case "Foggy":  return "low";
            default:       return "none";
        }
        return "none";
    }

    string GetSniperEffectiveness()
    {
        float vis = GetVisibilityRange();
        if (m_fWindSpeed > 10) vis *= 0.7;

        if (vis >= 1500) return "full";
        if (vis >= 800)  return "degraded";
        if (vis >= 300)  return "severely_degraded";
        return "impossible";
    }

    bool IsAirSupportAvailable()
    {
        return (m_sCurrentWeather != "Storm" && m_sCurrentWeather != "Foggy");
    }

    // Sun bearing approximation for tactical glare advantage
    float GetSunBearing()
    {
        float tod = GetTimeOfDay();
        if (tod >= 6 && tod <= 18)
            return 90 + ((tod - 6) / 12.0) * 180;
        return -1; // below horizon
    }

    string GetGlareAdvantageDirection()
    {
        float bearing = GetSunBearing();
        if (bearing < 0) return "none";
        if (bearing >= 45 && bearing < 135) return "east";
        if (bearing >= 135 && bearing < 225) return "south";
        if (bearing >= 225 && bearing < 315) return "west";
        return "north";
    }

    string GetAmbientLightLevel()
    {
        if (IsNightTime())
        {
            if (m_sCurrentWeather == "Clear") return "dim";
            return "dark";
        }
        if (m_sCurrentWeather == "Storm" || m_sCurrentWeather == "Foggy") return "dim";
        return "bright";
    }

    // ═══════════════════════════════════════════════
    // Tactical Lighting Presets
    // ═══════════════════════════════════════════════

    void SetTacticalLighting(string preset, float transitionSeconds)
    {
        transitionSeconds = Math.Clamp(transitionSeconds, 5, 300);

        if (preset == "sun_glare_east") SetTimeOfDay(7.5);
        else if (preset == "sun_glare_west") SetTimeOfDay(17.5);
        else if (preset == "golden_hour") SetTimeOfDay(18.0);
        else if (preset == "blue_hour") SetTimeOfDay(19.5);
        else if (preset == "moonlit_night")
        {
            SetTimeOfDay(23.0);
            SetWeather("Clear", 30, 600, true);
        }
        else if (preset == "pitch_black")
        {
            SetTimeOfDay(2.0);
            SetWeather("Overcast", 30, 600, true);
        }
        else if (preset == "dawn_assault") SetTimeOfDay(5.5);
        else
            Print("[GodMode:Weather] Unknown lighting preset: " + preset, LogLevel.WARNING);

        Print("[GodMode:Weather] Tactical lighting → " + preset);
    }

    // Gradual time transition (time lapse effect)
    void TransitionTimeTo(float targetHours, float realSeconds)
    {
        float currentTime = GetTimeOfDay();
        float delta = targetHours - currentTime;
        int steps = Math.Max(1, Math.Floor(realSeconds / 5.0));
        float stepSize = delta / steps;

        for (int i = 1; i <= steps; i++)
        {
            float t = currentTime + (stepSize * i);
            if (t < 0) t += 24;
            if (t >= 24) t -= 24;

            GetGame().GetCallqueue().CallLater(SetTimeOfDay, i * 5000, false, t);
        }

        Print("[GodMode:Weather] Time transition " + currentTime + " → " + targetHours + " over " + realSeconds + "s");
    }

    // ═══════════════════════════════════════════════
    // Environment Mood (composite weather + time + wind)
    // ═══════════════════════════════════════════════

    void SetEnvironmentMood(string mood)
    {
        if (mood == "dread")
        {
            StartWeatherProgression("Foggy", 2, false);
            TransitionTimeTo(4.0, 120);
            SetWindDirect(3, 45);
        }
        else if (mood == "fury")
        {
            StartWeatherProgression("Storm", 1, false);
            SetWindDirect(18, Math.RandomFloat(0, 360));
        }
        else if (mood == "calm_before")
        {
            SetWeather("Cloudy", 60, 600, true);
            TransitionTimeTo(17.5, 60);
            SetWindDirect(1, 180);
        }
        else if (mood == "aftermath")
        {
            StartWeatherProgression("Clear", 5, false);
            TransitionTimeTo(8.0, 120);
            SetWindDirect(4, 90);
        }
        else if (mood == "hope")
        {
            SetWeather("Clear", 30, 600, true);
            TransitionTimeTo(6.0, 60);
            SetWindDirect(2, 0);
        }
        else if (mood == "isolation")
        {
            SetWeather("Foggy", 45, 600, true);
            SetWeather("Rainy", 60, 300, true);
            TransitionTimeTo(1.0, 60);
            SetWindDirect(8, 270);
        }
        else
        {
            Print("[GodMode:Weather] Unknown mood: " + mood, LogLevel.WARNING);
        }

        Print("[GodMode:Weather] Environment mood → " + mood);
    }
}
