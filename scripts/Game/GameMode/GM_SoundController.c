// GM_SoundController.c — Environmental Audio and Positional Sound Effects
// Plays sounds at world positions for atmospheric and tactical effects.

class GM_SoundController
{
    // ═══════════════════════════════════════════════
    // Thunder (atmospheric)
    // ═══════════════════════════════════════════════

    void PlayThunderNear(vector position)
    {
        // Offset for realism — thunder doesn't strike at exact coordinates
        position[0] += Math.RandomFloat(-200, 200);
        position[2] += Math.RandomFloat(-200, 200);

        SCR_SoundManagerEntity soundMgr = GetGame().GetSoundManagerEntity();
        if (!soundMgr)
        {
            Print("[GodMode:Sound] SoundManager unavailable", LogLevel.WARNING);
            return;
        }

        soundMgr.CreateAndPlayAudioSource(position, "SOUND_ENVIRONMENT_THUNDER");
        Print("[GodMode:Sound] Thunder at " + position.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Generic positional sound
    // ═══════════════════════════════════════════════

    void PlaySoundAt(string soundEventName, vector position)
    {
        if (soundEventName == "") return;

        SCR_SoundManagerEntity soundMgr = GetGame().GetSoundManagerEntity();
        if (!soundMgr)
        {
            Print("[GodMode:Sound] SoundManager unavailable", LogLevel.WARNING);
            return;
        }

        soundMgr.CreateAndPlayAudioSource(position, soundEventName);
        Print("[GodMode:Sound] Playing '" + soundEventName + "' at " + position.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Sound from Prefab (for complex/looped sounds)
    // ═══════════════════════════════════════════════

    void PlaySoundFromPrefab(ResourceName prefab, vector position, float lifetimeSeconds = 10.0)
    {
        Resource res = Resource.Load(prefab);
        if (!res || !res.IsValid())
        {
            Print("[GodMode:Sound] Prefab not found: " + prefab, LogLevel.WARNING);
            return;
        }

        EntitySpawnParams params = new EntitySpawnParams();
        params.TransformMode = ETransformMode.WORLD;
        params.Transform[3] = position;

        IEntity soundEntity = GetGame().SpawnEntityPrefab(res, GetGame().GetWorld(), params);
        if (!soundEntity) return;

        SoundComponent sc = SoundComponent.Cast(soundEntity.FindComponent(SoundComponent));
        if (sc)
            sc.SoundEvent("SOUND_DEFAULT");

        // Schedule cleanup
        GetGame().GetCallqueue().CallLater(
            SCR_EntityHelper.DeleteEntityAndChildren,
            lifetimeSeconds * 1000, false, soundEntity);
    }

    // ═══════════════════════════════════════════════
    // Convenience methods for common sounds
    // ═══════════════════════════════════════════════

    void PlayExplosionSound(vector position)
    {
        PlaySoundAt("SOUND_EXPLOSION_LARGE", position);
    }

    void PlayDistantGunfire(vector position)
    {
        PlaySoundAt("SOUND_WEAPON_FIRE", position);
    }

    void PlayAirRaidSiren(vector position)
    {
        PlaySoundAt("SOUND_ENVIRONMENT_SIREN", position);
    }
}
