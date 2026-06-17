// GM_ParticleController.c — Visual Effects: Explosions, Smoke, Fire, Flares
// Spawns particle effects at world positions with automatic cleanup.

class GM_ParticleController
{
    // ═══════════════════════════════════════════════
    // Explosions
    // ═══════════════════════════════════════════════

    void SpawnExplosion(vector position, string size = "large")
    {
        ResourceName ptcPrefab;
        float lifetime = 15.0;

        switch (size)
        {
            case "small":
                ptcPrefab = "{D3FF41D172E5E8E0}Prefabs/Weapons/Effects/Explosion_Small.et";
                lifetime = 10.0;
                break;
            case "large":
                ptcPrefab = "{9D4E4F31E36DB22D}Prefabs/Weapons/Effects/Explosion_Large.et";
                lifetime = 15.0;
                break;
            case "vehicle":
                ptcPrefab = "{B5F3A7C6DE04EB3E}Prefabs/Weapons/Effects/Explosion_Vehicle.et";
                lifetime = 20.0;
                break;
            default:
                ptcPrefab = "{D3FF41D172E5E8E0}Prefabs/Weapons/Effects/Explosion_Small.et";
                lifetime = 10.0;
        }

        IEntity ent = SpawnEffect(ptcPrefab, position, lifetime);
        if (ent)
            Print("[GodMode:Particle] Explosion (" + size + ") at " + position.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Smoke
    // ═══════════════════════════════════════════════

    void SpawnSmoke(vector position, string color = "white")
    {
        ResourceName smokePrefab;

        switch (color)
        {
            case "white":
                smokePrefab = "{1A2B3C4D5E6F7890}Prefabs/Weapons/Grenades/SmokeEffect_White.et";
                break;
            case "red":
                smokePrefab = "{AABBCCDD11223344}Prefabs/Weapons/Grenades/SmokeEffect_Red.et";
                break;
            case "green":
                smokePrefab = "{BBCCDDEE22334455}Prefabs/Weapons/Grenades/SmokeEffect_Green.et";
                break;
            default:
                smokePrefab = "{1A2B3C4D5E6F7890}Prefabs/Weapons/Grenades/SmokeEffect_White.et";
        }

        IEntity ent = SpawnEffect(smokePrefab, position, 60.0);
        if (ent)
            Print("[GodMode:Particle] Smoke (" + color + ") at " + position.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Flares (illumination)
    // ═══════════════════════════════════════════════

    void SpawnFlare(vector position)
    {
        // Spawn above the target area — Enforce Script doesn't support array[i] += val
        float flareAlt = position[1] + 200; // 200m altitude
        position[1] = flareAlt;

        ResourceName flarePrefab = "{5A6B7C8D9E0F1234}Prefabs/Weapons/Projectiles/Flare_Illum.et";
        IEntity ent = SpawnEffect(flarePrefab, position, 45.0);
        if (ent)
            Print("[GodMode:Particle] Flare at " + position.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Generic Particle Spawner
    // ═══════════════════════════════════════════════

    protected IEntity SpawnEffect(ResourceName prefab, vector position, float lifetimeSeconds = 30.0)
    {
        Resource res = Resource.Load(prefab);
        if (!res || !res.IsValid())
        {
            Print("[GodMode:Particle] Prefab not found: " + prefab, LogLevel.WARNING);
            return null;
        }

        EntitySpawnParams params = new EntitySpawnParams();
        params.TransformMode = ETransformMode.WORLD;
        params.Transform[3] = position;

        IEntity ent = GetGame().SpawnEntityPrefab(res, GetGame().GetWorld(), params);
        if (!ent)
        {
            Print("[GodMode:Particle] Failed to spawn effect", LogLevel.WARNING);
            return null;
        }

        // Auto-cleanup
        GetGame().GetCallqueue().CallLater(
            SCR_EntityHelper.DeleteEntityAndChildren,
            lifetimeSeconds * 1000, false, ent);

        return ent;
    }

    // ═══════════════════════════════════════════════
    // Direct .ptc File Spawner
    // ═══════════════════════════════════════════════

    void SpawnParticleFromPTC(ResourceName ptcPath, vector position, float lifetimeSeconds = 30.0)
    {
        ParticleEffectEntitySpawnParams spawnParams();
        spawnParams.Transform[3] = position;
        ParticleEffectEntity ptcEntity = ParticleEffectEntity.SpawnParticleEffect(ptcPath, spawnParams);
        if (!ptcEntity)
        {
            Print("[GodMode:Particle] Failed to spawn PTC: " + ptcPath, LogLevel.WARNING);
            return;
        }

        Print("[GodMode:Particle] Spawned PTC at " + position.ToString(false));

        GetGame().GetCallqueue().CallLater(
            SCR_EntityHelper.DeleteEntityAndChildren,
            lifetimeSeconds * 1000, false, ptcEntity);
    }
}
