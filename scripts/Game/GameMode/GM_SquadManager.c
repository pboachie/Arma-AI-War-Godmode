// GM_SquadManager.c — AI Squad Spawning, Waypoint Commands, and State Tracking
// Manages all LLM-controlled infantry groups with full tactical order support.

class GM_SquadManager
{
    protected ref map<string, SCR_AIGroup> m_mSquads = new map<string, SCR_AIGroup>();
    protected int m_iSquadCounter = 0;
    protected int m_iMaxSquads = 30;

    // Reference back to main component for event logging
    protected GM_GodModeComponent m_pOwner;

    void SetOwner(GM_GodModeComponent owner)
    {
        m_pOwner = owner;
    }

    // ═══════════════════════════════════════════════
    // Spawning
    // ═══════════════════════════════════════════════

    string SpawnGroup(string groupType, vector position)
    {
        if (m_mSquads.Count() >= m_iMaxSquads)
        {
            Print("[GodMode:Squad] At squad limit (" + m_iMaxSquads + "), cannot spawn", LogLevel.WARNING);
            return "";
        }

        ResourceName prefab = GetGroupPrefab(groupType);
        if (prefab == "")
        {
            Print("[GodMode:Squad] Unknown group type: " + groupType, LogLevel.WARNING);
            return "";
        }

        Resource res = Resource.Load(prefab);
        if (!res || !res.IsValid())
        {
            Print("[GodMode:Squad] Failed to load: " + prefab, LogLevel.ERROR);
            return "";
        }

        EntitySpawnParams params = new EntitySpawnParams();
        params.TransformMode = ETransformMode.WORLD;
        params.Transform[3] = position;

        // Snap to terrain
        float terrainY = GetGame().GetWorld().GetSurfaceY(position[0], position[2]);
        params.Transform[3][1] = terrainY;

        IEntity ent = GetGame().SpawnEntityPrefab(res, GetGame().GetWorld(), params);
        SCR_AIGroup group = SCR_AIGroup.Cast(ent);
        if (!group)
        {
            Print("[GodMode:Squad] Spawn produced non-group entity for: " + groupType, LogLevel.ERROR);
            if (ent) SCR_EntityHelper.DeleteEntityAndChildren(ent);
            return "";
        }

        m_iSquadCounter++;
        string sid = "squad_" + m_iSquadCounter;
        m_mSquads.Set(sid, group);

        // Subscribe to lifecycle events
        group.GetOnEmpty().Insert(OnSquadEmpty);
        group.GetOnAgentRemoved().Insert(OnAgentLost);

        Print("[GodMode:Squad] Spawned " + groupType + " → " + sid + " (" + group.GetAgentsCount() + " agents)");
        return sid;
    }

    void SpawnReinforcements(string groupType, vector position)
    {
        string sid = SpawnGroup(groupType, position);
        if (sid != "")
        {
            Print("[GodMode:Squad] Reinforcements arrived: " + sid);
            if (m_pOwner)
                m_pOwner.LogEvent("reinforcements_spawned:" + sid);
        }
    }

    // ═══════════════════════════════════════════════
    // Tactical Orders
    // ═══════════════════════════════════════════════

    void OrderMove(string squadId, vector destination)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;

        AIWaypoint wp = SpawnWaypoint(
            "{22A875E30470BD4F}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
            destination);
        if (!wp) return;

        wp.SetCompletionRadius(20);
        group.AddWaypoint(wp);
        Print("[GodMode:Squad] " + squadId + " → MOVE to " + destination.ToString(false));
    }

    void OrderAttack(string squadId, vector target)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;

        AIWaypoint wp = SpawnWaypoint(
            "{06E1B6EBD480C6E0}Prefabs/AI/Waypoints/AIWaypoint_Attack.et",
            target);
        if (!wp) return;

        wp.SetCompletionRadius(30);
        group.AddWaypoint(wp);
        Print("[GodMode:Squad] " + squadId + " → ATTACK at " + target.ToString(false));
    }

    void OrderDefend(string squadId, vector position)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;

        AIWaypoint wp = SpawnWaypoint(
            "{B3E7B883E4D9E678}Prefabs/AI/Waypoints/AIWaypoint_Defend.et",
            position);
        if (!wp) return;

        wp.SetCompletionRadius(50);
        group.AddWaypoint(wp);
        Print("[GodMode:Squad] " + squadId + " → DEFEND at " + position.ToString(false));
    }

    void OrderPatrol(string squadId, vector center)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;

        // Clear existing waypoints
        group.CompleteAllWaypoints();

        // Create a 4-point patrol circuit
        float radius = 150;
        for (int i = 0; i < 4; i++)
        {
            float angle = i * 90 * Math.DEG2RAD;
            vector patrolPt = center;
            patrolPt[0] = patrolPt[0] + Math.Cos(angle) * radius;
            patrolPt[2] = patrolPt[2] + Math.Sin(angle) * radius;
            patrolPt[1] = GetGame().GetWorld().GetSurfaceY(patrolPt[0], patrolPt[2]);

            AIWaypoint wp = SpawnWaypoint(
                "{22A875E30470BD4F}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
                patrolPt);
            if (wp)
            {
                wp.SetCompletionRadius(15);
                group.AddWaypoint(wp);
            }
        }

        // Cycle waypoint to loop
        AIWaypoint cycleWp = SpawnWaypoint(
            "{1B556F5FB6051E42}Prefabs/AI/Waypoints/AIWaypoint_Cycle.et",
            center);
        if (cycleWp)
            group.AddWaypoint(cycleWp);

        Print("[GodMode:Squad] " + squadId + " → PATROL around " + center.ToString(false));
    }

    void OrderSeekDestroy(string squadId, vector area)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;

        AIWaypoint wp = SpawnWaypoint(
            "{750A8D1695BD09B6}Prefabs/AI/Waypoints/AIWaypoint_SearchAndDestroy.et",
            area);
        if (!wp) return;

        wp.SetCompletionRadius(200);
        group.AddWaypoint(wp);
        Print("[GodMode:Squad] " + squadId + " → SEEK & DESTROY at " + area.ToString(false));
    }

    void OrderAmbush(string squadId, vector position, string params)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;

        // Move to position silently
        AIWaypoint moveWp = SpawnWaypoint(
            "{22A875E30470BD4F}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
            position);
        if (moveWp)
        {
            moveWp.SetCompletionRadius(10);
            group.AddWaypoint(moveWp);
        }

        // Then hold and engage
        AIWaypoint defendWp = SpawnWaypoint(
            "{B3E7B883E4D9E678}Prefabs/AI/Waypoints/AIWaypoint_Defend.et",
            position);
        if (defendWp)
        {
            defendWp.SetCompletionRadius(30);
            group.AddWaypoint(defendWp);
        }

        Print("[GodMode:Squad] " + squadId + " → AMBUSH at " + position.ToString(false));
    }

    void OrderRetreat(string squadId, vector fallbackPosition)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;

        // Clear all current orders
        group.CompleteAllWaypoints();

        AIWaypoint wp = SpawnWaypoint(
            "{22A875E30470BD4F}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
            fallbackPosition);
        if (wp)
        {
            wp.SetCompletionRadius(15);
            group.AddWaypointAt(wp, 0); // Priority insertion
        }

        Print("[GodMode:Squad] " + squadId + " → RETREAT to " + fallbackPosition.ToString(false));
    }

    // ═══════════════════════════════════════════════
    // Despawning
    // ═══════════════════════════════════════════════

    void DespawnGroup(string squadId)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group)
        {
            m_mSquads.Remove(squadId);
            return;
        }

        SCR_EntityHelper.DeleteEntityAndChildren(group);
        m_mSquads.Remove(squadId);
        Print("[GodMode:Squad] Despawned: " + squadId);
    }

    // ═══════════════════════════════════════════════
    // Event Handlers
    // ═══════════════════════════════════════════════

    protected void OnSquadEmpty(SCR_AIGroup group)
    {
        for (int i = 0; i < m_mSquads.Count(); i++)
        {
            if (m_mSquads.GetElement(i) == group)
            {
                string sid = m_mSquads.GetKey(i);
                m_mSquads.Remove(sid);
                Print("[GodMode:Squad] Wiped: " + sid);
                if (m_pOwner)
                    m_pOwner.LogEvent("squad_wiped:" + sid);
                break;
            }
        }
    }

    protected void OnAgentLost(SCR_AIGroup group, AIAgent agent)
    {
        // Find squad ID for logging
        for (int i = 0; i < m_mSquads.Count(); i++)
        {
            if (m_mSquads.GetElement(i) == group)
            {
                string sid = m_mSquads.GetKey(i);
                int remaining = group.GetAgentsCount();
                Print("[GodMode:Squad] " + sid + " lost agent, strength: " + remaining);
                if (m_pOwner)
                    m_pOwner.LogEvent("agent_lost:" + sid + ":strength=" + remaining);
                break;
            }
        }
    }

    // ═══════════════════════════════════════════════
    // SITREP Data Collection
    // ═══════════════════════════════════════════════

    ref array<ref GM_UnitState> GetFriendlyStates() { return CollectStates("us"); }
    ref array<ref GM_UnitState> GetEnemyStates() { return CollectStates("ussr"); }

    protected ref array<ref GM_UnitState> CollectStates(string faction)
    {
        ref array<ref GM_UnitState> states = {};
        ref array<string> toRemove = {};

        for (int i = 0; i < m_mSquads.Count(); i++)
        {
            SCR_AIGroup group = m_mSquads.GetElement(i);
            if (!group)
            {
                toRemove.Insert(m_mSquads.GetKey(i));
                continue;
            }

            GM_UnitState state = new GM_UnitState();
            state.unit_id = m_mSquads.GetKey(i);
            state.unit_type = "infantry_squad";
            state.position = group.GetOrigin();
            state.strength = group.GetAgentsCount();
            state.health_pct = (state.strength > 0) ? 1.0 : 0.0;
            state.faction = faction;
            state.current_order = "active";

            states.Insert(state);
        }

        foreach (string deadId : toRemove)
            m_mSquads.Remove(deadId);

        return states;
    }

    ref array<ref GM_ContactReport> GetContactReports()
    {
        // Placeholder: aggregate perception data from friendly squads
        ref array<ref GM_ContactReport> contacts = {};
        return contacts;
    }

    // ═══════════════════════════════════════════════
    // Helpers
    // ═══════════════════════════════════════════════

    protected SCR_AIGroup GetGroup(string squadId)
    {
        SCR_AIGroup group;
        if (!m_mSquads.Find(squadId, group) || !group)
        {
            Print("[GodMode:Squad] Not found: " + squadId, LogLevel.WARNING);
            return null;
        }
        return group;
    }

    protected AIWaypoint SpawnWaypoint(ResourceName prefab, vector position)
    {
        Resource res = Resource.Load(prefab);
        if (!res || !res.IsValid())
        {
            Print("[GodMode:Squad] Failed to load waypoint: " + prefab, LogLevel.ERROR);
            return null;
        }

        EntitySpawnParams params = new EntitySpawnParams();
        params.TransformMode = ETransformMode.WORLD;
        params.Transform[3] = position;

        IEntity ent = GetGame().SpawnEntityPrefab(res, GetGame().GetWorld(), params);
        return AIWaypoint.Cast(ent);
    }

    int GetSquadCount() { return m_mSquads.Count(); }
    void SetMaxSquads(int max) { m_iMaxSquads = max; }

    // ═══════════════════════════════════════════════
    // Prefab Lookup
    // ═══════════════════════════════════════════════
    // NOTE: Verify GUIDs in Workbench Resource Browser for your game version.

    protected ResourceName GetGroupPrefab(string type)
    {
        switch (type)
        {
            // ── US Squads ──
            case "us_rifle_squad":
                return "{84ED40B0AE3B9E51}Prefabs/Groups/BLUFOR/Group_US_RifleSquad.et";
            case "us_fire_team":
                return "{6240B9613DB5CB64}Prefabs/Groups/BLUFOR/Group_US_FireTeam.et";
            case "us_mg_team":
                return "{F2C0CF43B06ED63E}Prefabs/Groups/BLUFOR/Group_US_MachineGunTeam.et";
            case "us_at_team":
                return "{BB1CB7E7B0AE7E53}Prefabs/Groups/BLUFOR/Group_US_AntiTankTeam.et";
            case "us_sniper_team":
                return "{3EB8B66F7A975CC2}Prefabs/Groups/BLUFOR/Group_US_SniperTeam.et";

            // ── USSR Squads ──
            case "ussr_rifle_squad":
                return "{E552DABF3636C2AD}Prefabs/Groups/OPFOR/Group_USSR_RifleSquad.et";
            case "ussr_fire_team":
                return "{A33FCB57E82D063C}Prefabs/Groups/OPFOR/Group_USSR_FireTeam.et";
            case "ussr_mg_team":
                return "{1A6EC0F7F9F48F34}Prefabs/Groups/OPFOR/Group_USSR_MachineGunTeam.et";
            case "ussr_at_team":
                return "{07C1D8EE7FDBBAEE}Prefabs/Groups/OPFOR/Group_USSR_AntiTankTeam.et";
            case "ussr_sniper_team":
                return "{55C46EC94DD16E3A}Prefabs/Groups/OPFOR/Group_USSR_SniperTeam.et";

            // ── FIA ──
            case "fia_squad":
                return "{C02F81D15E9D8B85}Prefabs/Groups/INDFOR/Group_FIA_RifleSquad.et";

            default:
                Print("[GodMode:Squad] Unknown group type: " + type + ", defaulting to USSR rifle squad", LogLevel.WARNING);
                return "{E552DABF3636C2AD}Prefabs/Groups/OPFOR/Group_USSR_RifleSquad.et";
        }
    }
}
