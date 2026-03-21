// GM_CleanupSystem.c — Entity Garbage Collection and Lifecycle Management
// Supplements the built-in SCR_GarbageSystem with active cleanup of LLM-spawned entities.

class GM_CleanupEntry
{
    IEntity entity;
    float cleanupTime;
    int retryCount;

    void GM_CleanupEntry(IEntity ent, float time)
    {
        entity = ent;
        cleanupTime = time;
        retryCount = 0;
    }
}

// ID-based cleanup entry — stores entity ID instead of raw reference
// to prevent access violations when entities are deleted before cleanup fires
class GM_CleanupEntryById
{
    int entityId;
    float cleanupTime;
    int retryCount;

    void GM_CleanupEntryById(int id, float time)
    {
        entityId = id;
        cleanupTime = time;
        retryCount = 0;
    }
}

class GM_CleanupSystem
{
    protected ref array<ref GM_CleanupEntry> m_aCleanupQueue;
    protected ref array<ref GM_CleanupEntryById> m_aCleanupByIdQueue;
    protected float m_fGameTime = 0;

    // Configurable limits
    protected const int MAX_CLEANUP_PER_PASS = 5;       // Max deletions per pass
    protected const int MAX_RETRY_COUNT = 10;            // Give up after this many retries
    protected const float RETRY_DELAY = 30.0;            // Seconds to wait before retry
    protected const float PLAYER_SAFE_RADIUS = 100.0;    // Don't delete near players
    protected const float DEFAULT_BODY_LIFETIME = 180.0;  // 3 minutes
    protected const float DEFAULT_WRECK_LIFETIME = 300.0; // 5 minutes

    void GM_CleanupSystem()
    {
        m_aCleanupQueue = {};
        m_aCleanupByIdQueue = {};
    }

    // ═══════════════════════════════════════════════
    // Schedule Cleanup
    // ═══════════════════════════════════════════════

    void ScheduleCleanup(IEntity entity, float delaySeconds)
    {
        if (!entity) return;

        float cleanupAt = m_fGameTime + delaySeconds;
        m_aCleanupQueue.Insert(new GM_CleanupEntry(entity, cleanupAt));
        Print("[GodMode:Cleanup] Scheduled entity for cleanup in " + delaySeconds + "s");
    }

    // ID-based scheduling — stores entity ID instead of raw reference.
    // Prevents access violations when the engine deletes the entity
    // before the cleanup timer fires.
    void ScheduleCleanupById(int entityId, float delaySeconds)
    {
        if (entityId == 0) return;

        float cleanupAt = m_fGameTime + delaySeconds;
        m_aCleanupByIdQueue.Insert(new GM_CleanupEntryById(entityId, cleanupAt));
        Print("[GodMode:Cleanup] Scheduled entity ID " + entityId + " for cleanup in " + delaySeconds + "s");
    }

    // ═══════════════════════════════════════════════
    // Periodic Cleanup Pass (called from EOnFrame)
    // ═══════════════════════════════════════════════

    void RunCleanupPass()
    {
        m_fGameTime += 10.0; // Approximate, called every ~10s

        int cleaned = 0;

        for (int i = m_aCleanupQueue.Count() - 1; i >= 0; i--)
        {
            if (cleaned >= MAX_CLEANUP_PER_PASS) break;

            GM_CleanupEntry entry = m_aCleanupQueue[i];

            // Entity already gone
            if (!entry.entity)
            {
                m_aCleanupQueue.Remove(i);
                continue;
            }

            // Not yet time
            if (m_fGameTime < entry.cleanupTime)
                continue;

            // Check player proximity
            if (IsPlayerNearby(entry.entity.GetOrigin(), PLAYER_SAFE_RADIUS))
            {
                entry.retryCount++;
                if (entry.retryCount >= MAX_RETRY_COUNT)
                {
                    // Force cleanup after too many retries
                    SCR_EntityHelper.DeleteEntityAndChildren(entry.entity);
                    m_aCleanupQueue.Remove(i);
                    cleaned++;
                    Print("[GodMode:Cleanup] Force-cleaned after max retries");
                }
                else
                {
                    // Defer cleanup
                    entry.cleanupTime = m_fGameTime + RETRY_DELAY;
                }
                continue;
            }

            // Safe to delete
            SCR_EntityHelper.DeleteEntityAndChildren(entry.entity);
            m_aCleanupQueue.Remove(i);
            cleaned++;
        }

        // Process ID-based cleanup queue
        for (int j = m_aCleanupByIdQueue.Count() - 1; j >= 0; j--)
        {
            if (cleaned >= MAX_CLEANUP_PER_PASS) break;

            GM_CleanupEntryById idEntry = m_aCleanupByIdQueue[j];

            // Not yet time
            if (m_fGameTime < idEntry.cleanupTime)
                continue;

            // Look up entity by ID — if already gone, silently discard
            IEntity resolvedEntity = GetGame().GetWorld().FindEntityByID(idEntry.entityId);
            if (!resolvedEntity)
            {
                m_aCleanupByIdQueue.Remove(j);
                continue;
            }

            // Check player proximity
            if (IsPlayerNearby(resolvedEntity.GetOrigin(), PLAYER_SAFE_RADIUS))
            {
                idEntry.retryCount++;
                if (idEntry.retryCount >= MAX_RETRY_COUNT)
                {
                    SCR_EntityHelper.DeleteEntityAndChildren(resolvedEntity);
                    m_aCleanupByIdQueue.Remove(j);
                    cleaned++;
                    Print("[GodMode:Cleanup] Force-cleaned entity ID " + idEntry.entityId + " after max retries");
                }
                else
                {
                    idEntry.cleanupTime = m_fGameTime + RETRY_DELAY;
                }
                continue;
            }

            // Safe to delete
            SCR_EntityHelper.DeleteEntityAndChildren(resolvedEntity);
            m_aCleanupByIdQueue.Remove(j);
            cleaned++;
        }

        int totalRemaining = m_aCleanupQueue.Count() + m_aCleanupByIdQueue.Count();
        if (cleaned > 0)
            Print("[GodMode:Cleanup] Pass complete: " + cleaned + " entities cleaned, " + totalRemaining + " remaining");
    }

    // ═══════════════════════════════════════════════
    // On-Demand Cleanup
    // ═══════════════════════════════════════════════

    void CleanupAllDead()
    {
        // Iterate tracked cleanup queue for dead entities
        int cleaned = 0;
        for (int i = m_aCleanupQueue.Count() - 1; i >= 0; i--)
        {
            GM_CleanupEntry entry = m_aCleanupQueue[i];
            if (!entry.entity)
            {
                m_aCleanupQueue.Remove(i);
                continue;
            }

            if (!IsPlayerNearby(entry.entity.GetOrigin(), 50.0))
            {
                SCR_EntityHelper.DeleteEntityAndChildren(entry.entity);
                m_aCleanupQueue.Remove(i);
                cleaned++;
            }
        }
        Print("[GodMode:Cleanup] CleanupAllDead: " + cleaned + " entities removed");
    }

    void CleanupRadius(vector center, float radius)
    {
        if (radius <= 0) radius = 50.0;
        radius = Math.Min(radius, 500.0); // Safety cap

        int cleaned = 0;
        for (int i = m_aCleanupQueue.Count() - 1; i >= 0; i--)
        {
            GM_CleanupEntry entry = m_aCleanupQueue[i];
            if (!entry.entity)
            {
                m_aCleanupQueue.Remove(i);
                continue;
            }

            float dist = vector.Distance(entry.entity.GetOrigin(), center);
            if (dist <= radius)
            {
                SCR_EntityHelper.DeleteEntityAndChildren(entry.entity);
                m_aCleanupQueue.Remove(i);
                cleaned++;
            }
        }
        Print("[GodMode:Cleanup] CleanupRadius(" + radius + "m): " + cleaned + " entities removed");
    }

    // ═══════════════════════════════════════════════
    // Force Delete (immediate, no checks)
    // ═══════════════════════════════════════════════

    static void ForceDelete(IEntity entity)
    {
        if (!entity) return;
        SCR_EntityHelper.DeleteEntityAndChildren(entity);
    }

    // ═══════════════════════════════════════════════
    // Player Proximity Check
    // ═══════════════════════════════════════════════

    protected bool IsPlayerNearby(vector position, float radius)
    {
        array<int> playerIds = {};
        GetGame().GetPlayerManager().GetPlayers(playerIds);

        foreach (int pid : playerIds)
        {
            IEntity playerEntity = GetGame().GetPlayerManager().GetPlayerControlledEntity(pid);
            if (!playerEntity) continue;

            float dist = vector.Distance(playerEntity.GetOrigin(), position);
            if (dist < radius) return true;
        }
        return false;
    }

    // ═══════════════════════════════════════════════
    // Status
    // ═══════════════════════════════════════════════

    int GetQueueSize() { return m_aCleanupQueue.Count() + m_aCleanupByIdQueue.Count(); }
}
