// GM_NarrativeManager.c — Player Notifications, Radio Messages, and Objectives
// Delivers LLM-generated narrative to players via hints, notifications, and tasks.

class GM_NarrativeManager
{
    // Track active objectives for SITREP
    protected ref map<string, string> m_mActiveObjectives = new map<string, string>();
    protected ref map<string, vector> m_mObjectivePositions = new map<string, vector>();
    protected ref map<string, string> m_mObjectiveTypes = new map<string, string>();
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
    // Notifications (radio comms, intel, faction comms)
    // ═══════════════════════════════════════════════

    void ShowNotification(string message)
    {
        if (message == "") return;

        SCR_HintManagerComponent.ShowCustomHint(message, "INTEL", 8.0);
        Print("[GodMode:Narrative] Notification: " + message);
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
        m_mObjectivePositions.Set(objId, position);
        m_mObjectiveTypes.Set(objId, taskType);
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
            m_mObjectivePositions.Remove(objectiveId);
            m_mObjectiveTypes.Remove(objectiveId);
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
            m_mObjectivePositions.Remove(objectiveId);
            m_mObjectiveTypes.Remove(objectiveId);
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

    void AppendObjectiveAnchors(array<ref GM_SpawnAnchor> outAnchors)
    {
        if (!outAnchors)
            return;

        for (int i = 0; i < m_mActiveObjectives.Count(); i++)
        {
            string objId = m_mActiveObjectives.GetKey(i);
            string description = m_mActiveObjectives.GetElement(i);

            vector position = vector.Zero;
            m_mObjectivePositions.Find(objId, position);
            if (position == vector.Zero)
                continue;

            string taskType = "objective";
            m_mObjectiveTypes.Find(objId, taskType);

            GM_SpawnAnchor anchor = new GM_SpawnAnchor();
            anchor.anchor_id = objId;
            anchor.name = description;
            anchor.position = position;
            anchor.anchor_type = "neutral_base";
            anchor.control_state = "neutral";
            anchor.tactical_radius = 150.0;
            anchor.priority = 0.65;
            anchor.notes = "Active objective anchor (" + taskType + ").";
            outAnchors.Insert(anchor);
        }
    }

    // ═══════════════════════════════════════════════
    // Prefab Lookup
    // ═══════════════════════════════════════════════

    protected ResourceName GetTaskPrefab(string taskType)
    {
        // NOTE: These are placeholder paths. Replace with actual task prefab
        // GUIDs from your Workbench Resource Browser.
        if (taskType == "move")
            return "";
        else if (taskType == "clear")
            return "";
        else if (taskType == "defend")
            return "";

        return "";
    }
}
