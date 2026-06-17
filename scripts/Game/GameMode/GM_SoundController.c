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
        float px = position[0] + Math.RandomFloat(-200, 200);
        float pz = position[2] + Math.RandomFloat(-200, 200);
        position = Vector(px, position[1], pz);

        // TODO: Hook into actual sound playback when AudioSystem API is available
        Print("[GodMode:Sound] Thunder at " + position.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Generic positional sound
    // ═══════════════════════════════════════════════

    void PlaySoundAt(string soundEventName, vector position)
    {
        if (soundEventName == "") return;

        // TODO: Hook into actual sound playback when AudioSystem API is available
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
