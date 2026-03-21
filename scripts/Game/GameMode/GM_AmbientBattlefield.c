// GM_AmbientBattlefield.c — Ambient battlefield effects for immersive warzone atmosphere
// Creates distant combat sounds, smoke columns, and environmental audio at configurable intensity.

class GM_AmbientBattlefield
{
    protected GM_SoundController m_SoundCtrl;
    protected GM_ParticleController m_ParticleCtrl;
    protected float m_fAmbientTimer;
    protected float m_fAmbientInterval;
    protected bool m_bActive;
    protected int m_iIntensity; // 0=quiet, 1=sporadic, 2=moderate, 3=heavy

    // Track active effects for SITREP
    protected int m_iActiveFires;
    protected int m_iActiveSmokeColumns;
    protected bool m_bArtilleryActive;

    void Init(GM_SoundController soundCtrl, GM_ParticleController particleCtrl)
    {
        m_SoundCtrl = soundCtrl;
        m_ParticleCtrl = particleCtrl;
        m_fAmbientTimer = 0;
        m_iIntensity = 0;
        m_bActive = false;
        m_iActiveFires = 0;
        m_iActiveSmokeColumns = 0;
        m_bArtilleryActive = false;
        SetIntensity(0);
    }

    void SetIntensity(int level)
    {
        m_iIntensity = Math.Clamp(level, 0, 3);
        m_bActive = (level > 0);
        switch (level)
        {
            case 0: m_fAmbientInterval = 999; break;  // effectively off
            case 1: m_fAmbientInterval = 45; break;   // sporadic
            case 2: m_fAmbientInterval = 20; break;   // moderate
            case 3: m_fAmbientInterval = 8; break;    // heavy
        }
        Print("[GodMode:Ambient] Intensity set to " + level);
    }

    int GetIntensity() { return m_iIntensity; }
    int GetActiveFires() { return m_iActiveFires; }
    int GetActiveSmokeColumns() { return m_iActiveSmokeColumns; }
    bool IsArtilleryActive() { return m_bArtilleryActive; }

    // Get battlefield noise level label for SITREP
    string GetNoiseLevel()
    {
        switch (m_iIntensity)
        {
            case 0: return "quiet";
            case 1: return "sporadic";
            case 2: return "moderate";
            case 3: return "heavy";
        }
        return "quiet";
    }

    void Tick(float deltaTime, vector playerCenter)
    {
        if (!m_bActive || m_iIntensity == 0) return;
        m_fAmbientTimer += deltaTime;
        if (m_fAmbientTimer < m_fAmbientInterval) return;
        m_fAmbientTimer = 0;

        // Generate a random ambient event at 500-2000m from players
        float dist = Math.RandomFloat(500, 2000);
        float angle = Math.RandomFloat(0, 360) * Math.DEG2RAD;
        vector pos = playerCenter;
        pos[0] = pos[0] + Math.Cos(angle) * dist;
        pos[2] = pos[2] + Math.Sin(angle) * dist;
        pos[1] = 0;

        int roll = Math.RandomInt(0, 100);

        if (roll < 40)
        {
            // Distant gunfire burst
            if (m_SoundCtrl)
                m_SoundCtrl.PlaySoundAt("SOUND_WEAPON_FIRE", pos);
        }
        else if (roll < 60)
        {
            // Distant explosion
            if (m_SoundCtrl)
                m_SoundCtrl.PlaySoundAt("SOUND_EXPLOSION_DISTANT", pos);
        }
        else if (roll < 75)
        {
            // Artillery rumble
            if (m_SoundCtrl)
                m_SoundCtrl.PlaySoundAt("SOUND_ARTILLERY_DISTANT", pos);
            m_bArtilleryActive = true;
            // Reset artillery flag after 30 seconds
            GetGame().GetCallqueue().CallLater(ResetArtilleryFlag, 30000, false);
        }
        else if (roll < 85 && m_iIntensity >= 2)
        {
            // Smoke column on horizon
            vector smokePos = pos;
            smokePos[1] = GetGame().GetWorld().GetSurfaceY(pos[0], pos[2]);
            if (m_ParticleCtrl)
            {
                m_ParticleCtrl.SpawnSmoke(smokePos, "black");
                m_iActiveSmokeColumns++;
                // Auto-decrement after 120 seconds
                GetGame().GetCallqueue().CallLater(DecrementSmokeCount, 120000, false);
            }
        }
        else if (roll < 95 && m_iIntensity >= 2)
        {
            // Aircraft flyover (high altitude sound)
            pos[1] = 500;
            if (m_SoundCtrl)
                m_SoundCtrl.PlaySoundAt("SOUND_AIRCRAFT_JET_DISTANT", pos);
        }
        else if (m_iIntensity >= 3)
        {
            // Helicopter in distance
            if (m_SoundCtrl)
                m_SoundCtrl.PlaySoundAt("SOUND_HELICOPTER_DISTANT", pos);
        }
    }

    // ═══════════════════════════════════════════════
    // Artillery Barrage Effect
    // ═══════════════════════════════════════════════

    void ExecuteArtilleryBarrage(vector center, float radius, int rounds,
                                  float durationSeconds, string caliber)
    {
        // Clamp for safety
        radius = Math.Clamp(radius, 10, 500);
        rounds = Math.Clamp(rounds, 1, 50);
        durationSeconds = Math.Clamp(durationSeconds, 5, 120);

        float intervalMs = (durationSeconds / rounds) * 1000;
        m_bArtilleryActive = true;

        string size = "large";
        if (caliber == "mortar") size = "small";
        else if (caliber == "heavy") size = "vehicle";

        for (int i = 0; i < rounds; i++)
        {
            // Randomize impact within radius
            float impactAngle = Math.RandomFloat(0, 360) * Math.DEG2RAD;
            float impactDist = Math.RandomFloat(0, radius) * Math.RandomFloat(0.5, 1.0);
            vector impact = center;
            impact[0] = impact[0] + Math.Cos(impactAngle) * impactDist;
            impact[2] = impact[2] + Math.Sin(impactAngle) * impactDist;
            impact[1] = GetGame().GetWorld().GetSurfaceY(impact[0], impact[2]);

            GetGame().GetCallqueue().CallLater(FireArtilleryRound, i * intervalMs, false,
                impact, size);
        }

        // Reset artillery flag after barrage completes
        float totalMs = durationSeconds * 1000 + 5000;
        GetGame().GetCallqueue().CallLater(ResetArtilleryFlag, totalMs, false);

        Print("[GodMode:Ambient] Artillery barrage: " + rounds + " rounds of " + caliber
              + " over " + durationSeconds + "s at radius " + radius + "m");
    }

    protected void FireArtilleryRound(vector position, string size)
    {
        // Incoming whistle sound
        if (m_SoundCtrl)
        {
            vector whistlePos = position;
            whistlePos[1] = whistlePos[1] + 100;
            m_SoundCtrl.PlaySoundAt("SOUND_ARTILLERY_INCOMING", whistlePos);
        }

        // Delayed impact (whistle travel time ~1.5s)
        GetGame().GetCallqueue().CallLater(ArtilleryImpact, 1500, false, position, size);
    }

    protected void ArtilleryImpact(vector position, string size)
    {
        if (m_ParticleCtrl)
            m_ParticleCtrl.SpawnExplosion(position, size);
        if (m_SoundCtrl)
            m_SoundCtrl.PlayExplosionSound(position);
    }

    // ═══════════════════════════════════════════════
    // Building Fire Effect
    // ═══════════════════════════════════════════════

    void StartBuildingFire(vector position, string intensity, float durationMinutes)
    {
        durationMinutes = Math.Clamp(durationMinutes, 1, 60);
        m_iActiveFires++;

        // Spawn fire particle + smoke column
        if (m_ParticleCtrl)
        {
            m_ParticleCtrl.SpawnSmoke(position, "black");
            // Additional fire particle at same location
            m_ParticleCtrl.SpawnSmoke(position, "fire");
        }

        // Auto-cleanup after duration
        float cleanupMs = durationMinutes * 60 * 1000;
        GetGame().GetCallqueue().CallLater(DecrementFireCount, cleanupMs, false);

        Print("[GodMode:Ambient] Building fire at " + position.ToString(false)
              + " intensity=" + intensity + " for " + durationMinutes + "min");
    }

    // ═══════════════════════════════════════════════
    // Air Strike Effect (simulated — audio + explosions, no aircraft entity)
    // ═══════════════════════════════════════════════

    void ExecuteAirStrike(vector target, string type, float approachBearing, int passes)
    {
        passes = Math.Clamp(passes, 1, 3);
        float bearingRad = approachBearing * Math.DEG2RAD;

        for (int pass = 0; pass < passes; pass++)
        {
            float passDelayMs = pass * 15000; // 15s between passes

            // Jet flyover sound from approach bearing
            vector approachPos = target;
            approachPos[0] = approachPos[0] - Math.Sin(bearingRad) * 2000;
            approachPos[2] = approachPos[2] - Math.Cos(bearingRad) * 2000;
            approachPos[1] = 300;

            GetGame().GetCallqueue().CallLater(AirStrikePass, passDelayMs, false,
                target, approachPos, type, bearingRad);
        }

        Print("[GodMode:Ambient] Air strike: " + type + " from bearing "
              + approachBearing + " (" + passes + " passes)");
    }

    protected void AirStrikePass(vector target, vector approachPos, string type, float bearingRad)
    {
        // Jet sound
        if (m_SoundCtrl)
            m_SoundCtrl.PlaySoundAt("SOUND_AIRCRAFT_JET_DISTANT", approachPos);

        // Explosions along strike line after 3 seconds (simulated bomb travel)
        int numImpacts = 4;
        if (type == "gun_run") numImpacts = 8;

        for (int i = 0; i < numImpacts; i++)
        {
            vector impactPos = target;
            float offset = (i - numImpacts / 2) * 30; // 30m spacing along strike line
            impactPos[0] = impactPos[0] + Math.Sin(bearingRad) * offset;
            impactPos[2] = impactPos[2] + Math.Cos(bearingRad) * offset;
            impactPos[1] = GetGame().GetWorld().GetSurfaceY(impactPos[0], impactPos[2]);

            string size = (type == "bomb_run") ? "vehicle" : "large";
            float delayMs = 3000 + i * 200; // sequential impacts
            GetGame().GetCallqueue().CallLater(ArtilleryImpact, delayMs, false, impactPos, size);
        }

        // Lingering smoke after strike
        GetGame().GetCallqueue().CallLater(SpawnStrikeSmoke, 5000, false, target);
    }

    protected void SpawnStrikeSmoke(vector position)
    {
        if (m_ParticleCtrl)
            m_ParticleCtrl.SpawnSmoke(position, "black");
    }

    // ═══════════════════════════════════════════════
    // Distant Combat Ambience (sustained firefight simulation)
    // ═══════════════════════════════════════════════

    void StartDistantCombat(vector position, string type, float durationSeconds, string intensity)
    {
        durationSeconds = Math.Clamp(durationSeconds, 10, 300);

        // Determine interval based on intensity
        float interval;
        if (intensity == "light") interval = 4000;
        else if (intensity == "heavy") interval = 1000;
        else interval = 2000; // moderate

        int bursts = Math.Floor(durationSeconds * 1000 / interval);
        bursts = Math.Clamp(bursts, 1, 100);

        for (int i = 0; i < bursts; i++)
        {
            float delayMs = i * interval + Math.RandomFloat(0, interval * 0.3);
            GetGame().GetCallqueue().CallLater(DistantCombatBurst, delayMs, false, position, type);
        }

        Print("[GodMode:Ambient] Distant " + type + " at " + position.ToString(false)
              + " for " + durationSeconds + "s (" + intensity + ")");
    }

    protected void DistantCombatBurst(vector position, string type)
    {
        if (!m_SoundCtrl) return;

        // Slight position randomization
        vector pos = position;
        pos[0] = pos[0] + Math.RandomFloat(-50, 50);
        pos[2] = pos[2] + Math.RandomFloat(-50, 50);

        if (type == "artillery_exchange")
            m_SoundCtrl.PlaySoundAt("SOUND_ARTILLERY_DISTANT", pos);
        else if (type == "air_battle")
            m_SoundCtrl.PlaySoundAt("SOUND_AIRCRAFT_JET_DISTANT", pos);
        else
            m_SoundCtrl.PlaySoundAt("SOUND_WEAPON_FIRE", pos);

        // Occasional explosion mixed in
        if (Math.RandomFloat(0, 1) < 0.2)
            m_SoundCtrl.PlaySoundAt("SOUND_EXPLOSION_DISTANT", pos);
    }

    // ═══════════════════════════════════════════════
    // Internal helpers
    // ═══════════════════════════════════════════════

    protected void ResetArtilleryFlag()
    {
        m_bArtilleryActive = false;
    }

    protected void DecrementSmokeCount()
    {
        m_iActiveSmokeColumns = Math.Max(0, m_iActiveSmokeColumns - 1);
    }

    protected void DecrementFireCount()
    {
        m_iActiveFires = Math.Max(0, m_iActiveFires - 1);
    }
}
