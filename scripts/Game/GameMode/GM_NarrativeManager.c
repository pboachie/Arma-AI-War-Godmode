// GM_NarrativeManager.c — Player Notifications, Radio Messages, and Objectives
// Delivers LLM-generated narrative to players via hints, notifications, and tasks.

class GM_NarrativeManager
{
    // Track active objectives for SITREP
    protected ref map<string, string> m_mActiveObjectives = new map<string, string>();
    protected int m_iObjectiveCounter = 0;

    // ═══════════════════════════════════════════════
    // Broadcast Messages
    // ═══════════════════════════════════════════════

    void BroadcastMessage(string message)
    {
        if (message == "") return;

        // Use SCR_HintManagerComponent for toast-style popup
        SCR_HintManagerComponent.ShowCustomHint(message, "COMMAND", 8.0);
        Print("[GodMode:Narrative] Broadcast: " + message);
    }

    void BroadcastUrgent(string message)
    {
        if (message == "") return;

        SCR_HintManagerComponent.ShowCustomHint("⚠ " + message, "URGENT", 12.0);
        Print("[GodMode:Narrative] URGENT: " + message);
    }

    // ═══════════════════════════════════════════════
    // Radio Messages
    // ═══════════════════════════════════════════════

    void SendRadioMessage(string message, string targetFaction = "all")
    {
        if (message == "") return;

        string prefix;
        switch (targetFaction)
        {
            case "us":     prefix = "[BLUFOR HQ] "; break;
            case "ussr":   prefix = "[OPFOR HQ] "; break;
            case "all":    prefix = "[RADIO] "; break;
            default:       prefix = "[RADIO] "; break;
        }

        SCR_HintManagerComponent.ShowCustomHint(prefix + message, "HQ", 10.0);
        Print("[GodMode:Narrative] Radio (" + targetFaction + "): " + message);
    }

    // ═══════════════════════════════════════════════
    // Objectives
    // ═══════════════════════════════════════════════

    string CreateObjective(string taskType, string description, vector position)
    {
        if (description == "") return "";

        m_iObjectiveCounter++;
        string objId = "obj_" + m_iObjectiveCounter;

        // Spawn task entity if prefabs are available
        ResourceName taskPrefab = GetTaskPrefab(taskType);
        if (taskPrefab != "")
        {
            Resource res = Resource.Load(taskPrefab);
            if (res && res.IsValid())
            {
                EntitySpawnParams params = new EntitySpawnParams();
                params.TransformMode = ETransformMode.WORLD;
                params.Transform[3] = position;

                GetGame().SpawnEntityPrefab(res, GetGame().GetWorld(), params);
            }
        }

        // Track and notify
        m_mActiveObjectives.Set(objId, description);
        BroadcastMessage("NEW OBJECTIVE: " + description);
        Print("[GodMode:Narrative] Objective created: " + objId + " — " + description);

        return objId;
    }

    void CompleteObjective(string objectiveId)
    {
        string description;
        if (m_mActiveObjectives.Find(objectiveId, description))
        {
            m_mActiveObjectives.Remove(objectiveId);
            BroadcastMessage("OBJECTIVE COMPLETE: " + description);
            Print("[GodMode:Narrative] Objective completed: " + objectiveId);
        }
        else
        {
            Print("[GodMode:Narrative] Unknown objective: " + objectiveId, LogLevel.WARNING);
        }
    }

    void FailObjective(string objectiveId)
    {
        string description;
        if (m_mActiveObjectives.Find(objectiveId, description))
        {
            m_mActiveObjectives.Remove(objectiveId);
            BroadcastUrgent("OBJECTIVE FAILED: " + description);
            Print("[GodMode:Narrative] Objective failed: " + objectiveId);
        }
    }

    // ═══════════════════════════════════════════════
    // Status for SITREP
    // ═══════════════════════════════════════════════

    string GetCurrentObjectiveSummary()
    {
        if (m_mActiveObjectives.Count() == 0) return "none";

        string summary = "";
        for (int i = 0; i < m_mActiveObjectives.Count(); i++)
        {
            if (i > 0) summary += " | ";
            summary += m_mActiveObjectives.GetKey(i) + ": " + m_mActiveObjectives.GetElement(i);
        }
        return summary;
    }

    int GetActiveObjectiveCount() { return m_mActiveObjectives.Count(); }

    // ═══════════════════════════════════════════════
    // Prefab Lookup
    // ═══════════════════════════════════════════════

    protected ResourceName GetTaskPrefab(string taskType)
    {
        // NOTE: These are placeholder paths. Replace with actual task prefab
        // GUIDs from your Workbench Resource Browser.
        switch (taskType)
        {
            case "move":    return "";
            case "clear":   return "";
            case "defend":  return "";
            default:        return "";
        }
    }
}
