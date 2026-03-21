// GM_SquadManager.c — AI Squad Spawning, Waypoint Commands, and State Tracking
// Manages all LLM-controlled infantry groups with full tactical order support.

class GM_SquadManager
{
    protected ref map<string, SCR_AIGroup> m_mSquads = new map<string, SCR_AIGroup>();
    protected int m_iSquadCounter = 0;
    protected int m_iMaxSquads = 30;

    // Reference back to main component for event logging
    protected GM_GodModeComponent m_pOwner;

    // ── Order Execution Tracking (Priority 1) ──
    protected ref map<string, string> m_mCurrentOrders;           // squad_id -> order type
    protected ref map<string, vector> m_mOrderTargets;            // squad_id -> target position
    protected ref map<string, float> m_mOrderTimestamps;          // squad_id -> time when order was issued

    // ── Per-Unit Combat Stats (Priority 6) ──
    protected ref map<string, int> m_mKillsSinceReport;           // squad_id -> kills this cycle
    protected ref map<string, int> m_mCasualtiesSinceReport;      // squad_id -> losses this cycle

    void Init()
    {
        m_mCurrentOrders = new map<string, string>();
        m_mOrderTargets = new map<string, vector>();
        m_mOrderTimestamps = new map<string, float>();
        m_mKillsSinceReport = new map<string, int>();
        m_mCasualtiesSinceReport = new map<string, int>();
    }

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

        // Initialize tracking for new squad
        m_mCurrentOrders.Set(sid, "idle");
        m_mOrderTargets.Set(sid, position);
        m_mOrderTimestamps.Set(sid, System.GetTickCount() / 1000.0);
        m_mKillsSinceReport.Set(sid, 0);
        m_mCasualtiesSinceReport.Set(sid, 0);

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

        m_mCurrentOrders.Set(squadId, "move");
        m_mOrderTargets.Set(squadId, destination);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
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

        m_mCurrentOrders.Set(squadId, "attack");
        m_mOrderTargets.Set(squadId, target);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
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

        m_mCurrentOrders.Set(squadId, "defend");
        m_mOrderTargets.Set(squadId, position);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
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

        m_mCurrentOrders.Set(squadId, "patrol");
        m_mOrderTargets.Set(squadId, center);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
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

        m_mCurrentOrders.Set(squadId, "seek_and_destroy");
        m_mOrderTargets.Set(squadId, area);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
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

        m_mCurrentOrders.Set(squadId, "ambush");
        m_mOrderTargets.Set(squadId, position);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
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

        m_mCurrentOrders.Set(squadId, "retreat");
        m_mOrderTargets.Set(squadId, fallbackPosition);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
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

        // Clean up tracking data
        m_mCurrentOrders.Remove(squadId);
        m_mOrderTargets.Remove(squadId);
        m_mOrderTimestamps.Remove(squadId);
        m_mKillsSinceReport.Remove(squadId);
        m_mCasualtiesSinceReport.Remove(squadId);

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

                // Track casualty
                int prev = 0;
                m_mCasualtiesSinceReport.Find(sid, prev);
                m_mCasualtiesSinceReport.Set(sid, prev + 1);
                break;
            }
        }
    }

    // ═══════════════════════════════════════════════
    // Combat Stats
    // ═══════════════════════════════════════════════

    void RecordKill(string squadId)
    {
        int prev = 0;
        m_mKillsSinceReport.Find(squadId, prev);
        m_mKillsSinceReport.Set(squadId, prev + 1);
    }

    void ResetCycleStats()
    {
        m_mKillsSinceReport.Clear();
        m_mCasualtiesSinceReport.Clear();
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

            // Order execution feedback
            string orderType = "idle";
            m_mCurrentOrders.Find(m_mSquads.GetKey(i), orderType);
            state.current_order = orderType;

            vector orderTarget = vector.Zero;
            m_mOrderTargets.Find(m_mSquads.GetKey(i), orderTarget);

            float dist = 0;
            if (orderTarget != vector.Zero)
                dist = vector.Distance(state.position, orderTarget);
            state.distance_to_objective = dist;

            float orderTime = 0;
            m_mOrderTimestamps.Find(m_mSquads.GetKey(i), orderTime);
            float now = System.GetTickCount() / 1000.0;
            state.time_since_order = (orderTime > 0) ? (now - orderTime) : 0;
            state.time_in_current_order = state.time_since_order;

            // Determine order status
            if (state.strength <= 0)
                state.order_status = "wiped_out";
            else if (dist < 30 && orderType != "idle")
                state.order_status = "arrived";
            else if (state.health_pct < 0.5)
                state.order_status = "engaged";
            else if (orderType == "idle")
                state.order_status = "idle";
            else
                state.order_status = "moving";

            // Combat stats
            int kills = 0;
            m_mKillsSinceReport.Find(m_mSquads.GetKey(i), kills);
            state.kills = kills;

            int casualties = 0;
            m_mCasualtiesSinceReport.Find(m_mSquads.GetKey(i), casualties);
            state.casualties = casualties;

            state.ammo_status = "full";  // TODO: query actual ammo when API available
            state.is_suppressed = (state.health_pct < 0.7 && state.order_status == "engaged");

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
