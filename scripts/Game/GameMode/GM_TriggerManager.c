// GM_TriggerManager.c — Spatial Trigger Zones for Reactive LLM Storytelling
// Creates and manages trigger zones that feed player movement events to the LLM.

class GM_TriggerManager
{
    protected ref array<SCR_BaseTriggerEntity> m_aTriggers;
    protected ref map<IEntity, string> m_mTriggerTags;
    protected GM_GodModeComponent m_pOwner;

    void GM_TriggerManager()
    {
        m_aTriggers = {};
        m_mTriggerTags = new map<IEntity, string>();
    }

    void SetOwner(GM_GodModeComponent owner)
    {
        m_pOwner = owner;
    }

    // ═══════════════════════════════════════════════
    // Create Trigger Zones
    // ═══════════════════════════════════════════════

    SCR_BaseTriggerEntity CreateTrigger(vector position, float radius, string eventTag)
    {
        ResourceName triggerPrefab =
            "{TRIGGER_PREFAB}Prefabs/ScenarioFramework/Triggers/SCR_CharacterTriggerEntity.et";

        Resource res = Resource.Load(triggerPrefab);
        if (!res || !res.IsValid())
        {
            Print("[GodMode:Trigger] Trigger prefab not available", LogLevel.WARNING);
            return null;
        }

        EntitySpawnParams params = new EntitySpawnParams();
        params.TransformMode = ETransformMode.WORLD;
        params.Transform[3] = position;

        IEntity ent = GetGame().SpawnEntityPrefab(res, GetGame().GetWorld(), params);
        SCR_BaseTriggerEntity trigger = SCR_BaseTriggerEntity.Cast(ent);
        if (!trigger)
        {
            if (ent) SCR_EntityHelper.DeleteEntityAndChildren(ent);
            Print("[GodMode:Trigger] Failed to create trigger", LogLevel.ERROR);
            return null;
        }

        trigger.SetSphereRadius(radius);
        trigger.GetOnActivate().Insert(OnTriggerActivated);
        trigger.GetOnDeactivate().Insert(OnTriggerDeactivated);

        m_aTriggers.Insert(trigger);
        m_mTriggerTags.Set(trigger, eventTag);

        Print("[GodMode:Trigger] Created '" + eventTag + "' at " +
              position.ToString(false) + " (r=" + radius + "m)");
        return trigger;
    }

    // ═══════════════════════════════════════════════
    // Event Callbacks
    // ═══════════════════════════════════════════════

    protected void OnTriggerActivated(IEntity trigger, IEntity activator)
    {
        string tag;
        if (m_mTriggerTags.Find(trigger, tag))
        {
            vector pos = trigger.GetOrigin();
            Print("[GodMode:Trigger] ACTIVATED: " + tag);

            if (m_pOwner)
                m_pOwner.LogEvent("trigger_entered:" + tag + ":pos=" + pos.ToString(false));
        }
    }

    protected void OnTriggerDeactivated(IEntity trigger, IEntity activator)
    {
        string tag;
        if (m_mTriggerTags.Find(trigger, tag))
        {
            vector pos = trigger.GetOrigin();
            Print("[GodMode:Trigger] DEACTIVATED: " + tag);

            if (m_pOwner)
                m_pOwner.LogEvent("trigger_exited:" + tag + ":pos=" + pos.ToString(false));
        }
    }

    // ═══════════════════════════════════════════════
    // Cleanup
    // ═══════════════════════════════════════════════

    void RemoveTrigger(SCR_BaseTriggerEntity trigger)
    {
        if (!trigger) return;

        int idx = m_aTriggers.Find(trigger);
        if (idx >= 0) m_aTriggers.Remove(idx);
        m_mTriggerTags.Remove(trigger);

        SCR_EntityHelper.DeleteEntityAndChildren(trigger);
    }

    void RemoveAllTriggers()
    {
        foreach (SCR_BaseTriggerEntity trigger : m_aTriggers)
        {
            if (trigger)
                SCR_EntityHelper.DeleteEntityAndChildren(trigger);
        }
        m_aTriggers.Clear();
        m_mTriggerTags.Clear();
        Print("[GodMode:Trigger] All triggers removed");
    }

    int GetTriggerCount() { return m_aTriggers.Count(); }
}
