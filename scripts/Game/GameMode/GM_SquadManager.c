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

    // ── Runtime prefab cache (auto-discovered from entity catalog) ──
    protected ref map<string, ResourceName> m_mCatalogGroups = new map<string, ResourceName>();
    protected bool m_bCatalogReady = false;

    void Init()
    {
        m_mCurrentOrders = new map<string, string>();
        m_mOrderTargets = new map<string, vector>();
        m_mOrderTimestamps = new map<string, float>();
        m_mKillsSinceReport = new map<string, int>();
        m_mCasualtiesSinceReport = new map<string, int>();
        m_mCatalogGroups = new map<string, ResourceName>();
    }

    // ── Lazy catalog discovery — runs once on first spawn ──
    protected void EnsureCatalog()
    {
        if (m_bCatalogReady) return;

        SCR_EntityCatalogManagerComponent catalogMgr = SCR_EntityCatalogManagerComponent.GetInstance();
        if (!catalogMgr) return;

        SCR_EntityCatalog groupCatalog = catalogMgr.GetEntityCatalogOfType(EEntityCatalogType.GROUP);
        if (!groupCatalog) return;

        array<SCR_EntityCatalogEntry> entries = new array<SCR_EntityCatalogEntry>();
        groupCatalog.GetEntityList(entries);
        if (entries.Count() == 0) return;

        foreach (SCR_EntityCatalogEntry entry : entries)
        {
            ResourceName prefab = entry.GetPrefab();
            string s = "" + prefab;

            // USSR groups
            if (s.Contains("USSR_RifleSquad"))      m_mCatalogGroups.Set("ussr_rifle_squad", prefab);
            else if (s.Contains("USSR_FireTeam"))    m_mCatalogGroups.Set("ussr_fire_team", prefab);
            else if (s.Contains("USSR_MachineGun") || s.Contains("USSR_MGTeam"))
                                                     m_mCatalogGroups.Set("ussr_mg_team", prefab);
            else if (s.Contains("USSR_AntiTank") || s.Contains("USSR_ATTeam"))
                                                     m_mCatalogGroups.Set("ussr_at_team", prefab);
            else if (s.Contains("USSR_Sniper"))      m_mCatalogGroups.Set("ussr_sniper_team", prefab);
            // US groups
            else if (s.Contains("US_RifleSquad"))    m_mCatalogGroups.Set("us_rifle_squad", prefab);
            else if (s.Contains("US_FireTeam"))      m_mCatalogGroups.Set("us_fire_team", prefab);
            else if (s.Contains("US_MachineGun") || s.Contains("US_MGTeam"))
                                                     m_mCatalogGroups.Set("us_mg_team", prefab);
            else if (s.Contains("US_AntiTank") || s.Contains("US_ATTeam"))
                                                     m_mCatalogGroups.Set("us_at_team", prefab);
            else if (s.Contains("US_Sniper"))        m_mCatalogGroups.Set("us_sniper_team", prefab);
            // FIA
            else if (s.Contains("FIA_RifleSquad"))   m_mCatalogGroups.Set("fia_squad", prefab);
        }

        m_bCatalogReady = (m_mCatalogGroups.Count() > 0);
        Print("[GodMode:Squad] Catalog discovery: found " + m_mCatalogGroups.Count() + " group prefabs");
        for (int i = 0; i < m_mCatalogGroups.Count(); i++)
            Print("[GodMode:Squad]   " + m_mCatalogGroups.GetKey(i) + " = " + m_mCatalogGroups.GetElement(i));
    }

    // Resolve prefab: catalog first, hardcoded fallback
    protected ResourceName ResolveGroupPrefab(string grpType)
    {
        EnsureCatalog();
        ResourceName found;
        if (m_mCatalogGroups.Find(grpType, found))
            return found;
        return GetGroupPrefab(grpType);
    }

    void SetOwner(GM_GodModeComponent owner)
    {
        m_pOwner = owner;
    }

    // ═══════════════════════════════════════════════
    // Spawning
    // ═══════════════════════════════════════════════

    // Remove squads that have 0 living agents (ghost squads)
    void PurgeDeadSquads()
    {
        ref array<string> toRemove = {};
        for (int i = 0; i < m_mSquads.Count(); i++)
        {
            string sid = m_mSquads.GetKey(i);
            SCR_AIGroup group = m_mSquads.GetElement(i);

            if (!group || group.GetAgentsCount() == 0)
                toRemove.Insert(sid);
        }

        foreach (string deadId : toRemove)
        {
            SCR_AIGroup deadGroup = null;
            m_mSquads.Find(deadId, deadGroup);
            if (deadGroup)
                SCR_EntityHelper.DeleteEntityAndChildren(deadGroup);
            m_mSquads.Remove(deadId);
            m_mCurrentOrders.Remove(deadId);
            m_mOrderTargets.Remove(deadId);
            m_mOrderTimestamps.Remove(deadId);
            m_mKillsSinceReport.Remove(deadId);
            m_mCasualtiesSinceReport.Remove(deadId);
        }

        if (toRemove.Count() > 0)
            Print("[GodMode:Squad] Purged " + toRemove.Count() + " dead/empty squads (now " + m_mSquads.Count() + " active)");
    }

    string SpawnGroup(string groupType, vector position)
    {
        // Clean up ghost squads before checking limit
        PurgeDeadSquads();

        if (m_mSquads.Count() >= m_iMaxSquads)
        {
            Print("[GodMode:Squad] At squad limit (" + m_iMaxSquads + ") even after purge, cannot spawn", LogLevel.WARNING);
            return "";
        }

        // Try catalog-discovered prefab first, then hardcoded
        ResourceName prefab = ResolveGroupPrefab(groupType);
        if (prefab == "")
        {
            Print("[GodMode:Squad] Unknown group type: " + groupType, LogLevel.WARNING);
            return "";
        }

        Resource res = Resource.Load(prefab);
        if (!res || !res.IsValid())
        {
            Print("[GodMode:Squad] Prefab unavailable: " + prefab + " — trying fallback", LogLevel.WARNING);
            string fbType = GetFallbackType(groupType);
            ResourceName fallback = ResolveGroupPrefab(fbType);
            if (fallback == "") fallback = GetFallbackPrefab(groupType);
            if (fallback != "")
            {
                res = Resource.Load(fallback);
                groupType = fbType;
            }
            if (!res || !res.IsValid())
            {
                Print("[GodMode:Squad] Fallback also failed", LogLevel.ERROR);
                return "";
            }
        }

        // Check if position is underwater — find dry land if so
        position = FindDryLandPosition(position);

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

        int agentCount = group.GetAgentsCount();
        Print("[GodMode:Squad] Spawned " + groupType + " → " + sid + " (" + agentCount + " agents)");

        // If the formation system failed to populate agents, manually spawn soldiers
        if (agentCount == 0)
        {
            int soldiersToSpawn = GetDesiredSquadSize(groupType);
            Print("[GodMode:Squad] Formation system produced 0 agents — manually spawning " + soldiersToSpawn + " soldiers");
            ManuallyPopulateGroup(group, groupType, position, soldiersToSpawn);
        }

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
            "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
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
            "{1B0E3436C30FA211}Prefabs/AI/Waypoints/AIWaypoint_Attack.et",
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
            "{93291E72AC23930F}Prefabs/AI/Waypoints/AIWaypoint_Defend.et",
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
                "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
                patrolPt);
            if (wp)
            {
                wp.SetCompletionRadius(15);
                group.AddWaypoint(wp);
            }
        }

        // Cycle waypoint to loop
        AIWaypoint cycleWp = SpawnWaypoint(
            "{35BD6541CBB8AC08}Prefabs/AI/Waypoints/AIWaypoint_Cycle.et",
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
            "{B3E7B8DC2BAB8ACC}Prefabs/AI/Waypoints/AIWaypoint_SearchAndDestroy.et",
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
            "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
            position);
        if (moveWp)
        {
            moveWp.SetCompletionRadius(10);
            group.AddWaypoint(moveWp);
        }

        // Then hold and engage
        AIWaypoint defendWp = SpawnWaypoint(
            "{93291E72AC23930F}Prefabs/AI/Waypoints/AIWaypoint_Defend.et",
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
            "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
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
    // Advanced Tactical Orders
    // ═══════════════════════════════════════════════

    void OrderSuppress(string squadId, vector target, string params)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;

        // Calculate a position 150m away from target facing it — units suppress FROM COVER,
        // not by charging into the target. Move to overwatch range, then defend (fire at will).
        vector groupPos = group.GetOrigin();
        vector toTarget = target - groupPos;
        float distToTarget = toTarget.Length();

        // If already within 100-250m, hold current position and engage.
        // Otherwise, move to a position ~150m from target along the approach axis.
        vector suppressPos;
        if (distToTarget >= 80 && distToTarget <= 300)
        {
            // Already at decent suppress range — hold here
            suppressPos = groupPos;
        }
        else
        {
            // Move to 150m from target along current approach direction
            float desiredDist = 150.0;
            if (distToTarget > 1.0)
            {
                vector dir = toTarget / distToTarget; // normalize
                suppressPos = target - dir * desiredDist;
            }
            else
            {
                suppressPos = groupPos; // too close to compute direction, stay put
            }
        }
        suppressPos[1] = GetGame().GetWorld().GetSurfaceY(suppressPos[0], suppressPos[2]);

        // Move to suppress position
        AIWaypoint moveWp = SpawnWaypoint(
            "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
            suppressPos);
        if (moveWp) { moveWp.SetCompletionRadius(15); group.AddWaypoint(moveWp); }

        // Then defend from that position — AI will engage enemies at the target area
        // while staying at their suppress position (not charging in)
        AIWaypoint defendWp = SpawnWaypoint(
            "{93291E72AC23930F}Prefabs/AI/Waypoints/AIWaypoint_Defend.et",
            suppressPos);
        if (defendWp) { defendWp.SetCompletionRadius(50); group.AddWaypoint(defendWp); }

        Print("[GodMode:Squad] " + squadId + " → SUPPRESS at " + target.ToString(false)
              + " from " + suppressPos.ToString(false) + " (dist=" + distToTarget + "m)");
        m_mCurrentOrders.Set(squadId, "suppress");
        m_mOrderTargets.Set(squadId, target);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
    }

    void OrderOverwatch(string squadId, vector position, string params)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;

        // Move to position first
        AIWaypoint moveWp = SpawnWaypoint(
            "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
            position);
        if (moveWp) { moveWp.SetCompletionRadius(10); group.AddWaypoint(moveWp); }

        // Then defend (overwatch)
        AIWaypoint defendWp = SpawnWaypoint(
            "{93291E72AC23930F}Prefabs/AI/Waypoints/AIWaypoint_Defend.et",
            position);
        if (defendWp) { defendWp.SetCompletionRadius(30); group.AddWaypoint(defendWp); }

        Print("[GodMode:Squad] " + squadId + " → OVERWATCH at " + position.ToString(false));
        m_mCurrentOrders.Set(squadId, "overwatch");
        m_mOrderTargets.Set(squadId, position);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
    }

    void OrderSilentMove(string squadId, vector destination)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;

        group.CompleteAllWaypoints();

        AIWaypoint wp = SpawnWaypoint(
            "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
            destination);
        if (!wp) return;

        wp.SetCompletionRadius(10);
        group.AddWaypoint(wp);

        Print("[GodMode:Squad] " + squadId + " → SILENT MOVE to " + destination.ToString(false));
        m_mCurrentOrders.Set(squadId, "silent_move");
        m_mOrderTargets.Set(squadId, destination);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
    }

    void OrderGarrison(string squadId, vector buildingPos)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;

        // Move to building position and defend (garrison behavior)
        AIWaypoint moveWp = SpawnWaypoint(
            "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
            buildingPos);
        if (moveWp) { moveWp.SetCompletionRadius(10); group.AddWaypoint(moveWp); }

        AIWaypoint defendWp = SpawnWaypoint(
            "{93291E72AC23930F}Prefabs/AI/Waypoints/AIWaypoint_Defend.et",
            buildingPos);
        if (defendWp) { defendWp.SetCompletionRadius(30); group.AddWaypoint(defendWp); }

        Print("[GodMode:Squad] " + squadId + " → GARRISON at " + buildingPos.ToString(false));
        m_mCurrentOrders.Set(squadId, "garrison");
        m_mOrderTargets.Set(squadId, buildingPos);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
    }

    void OrderEstablishOP(string squadId, vector position)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;

        AIWaypoint moveWp = SpawnWaypoint(
            "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et",
            position);
        if (moveWp) { moveWp.SetCompletionRadius(5); group.AddWaypoint(moveWp); }

        AIWaypoint defWp = SpawnWaypoint(
            "{93291E72AC23930F}Prefabs/AI/Waypoints/AIWaypoint_Defend.et",
            position);
        if (defWp) { defWp.SetCompletionRadius(20); group.AddWaypoint(defWp); }

        Print("[GodMode:Squad] " + squadId + " → ESTABLISH OP at " + position.ToString(false));
        m_mCurrentOrders.Set(squadId, "observation_post");
        m_mOrderTargets.Set(squadId, position);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
    }

    void OrderFightingWithdrawal(string squadId, string phaseLines)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;
        group.CompleteAllWaypoints();

        // Parse phase lines: "x1,z1;x2,z2;x3,z3"
        array<string> phases = {};
        phaseLines.Split(";", phases, false);

        foreach (string phase : phases)
        {
            array<string> coords = {};
            phase.Split(",", coords, false);
            if (coords.Count() < 2) continue;

            vector pos = Vector(coords[0].Trim().ToFloat(), 0, coords[1].Trim().ToFloat());
            pos[1] = GetGame().GetWorld().GetSurfaceY(pos[0], pos[2]);

            // Defend briefly at each phase line
            AIWaypoint defWp = SpawnWaypoint(
                "{93291E72AC23930F}Prefabs/AI/Waypoints/AIWaypoint_Defend.et", pos);
            if (defWp) { defWp.SetCompletionRadius(15); group.AddWaypoint(defWp); }

            // Then move to next
            AIWaypoint moveWp = SpawnWaypoint(
                "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et", pos);
            if (moveWp) { moveWp.SetCompletionRadius(15); group.AddWaypoint(moveWp); }
        }

        Print("[GodMode:Squad] " + squadId + " → FIGHTING WITHDRAWAL (" + phases.Count() + " phase lines)");
        m_mCurrentOrders.Set(squadId, "fighting_withdrawal");
        m_mOrderTargets.Set(squadId, group.GetOrigin());
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
    }

    void OrderPatrolCloverleaf(string squadId, vector center, float radius)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;
        group.CompleteAllWaypoints();

        if (radius <= 0) radius = 200;

        for (int lobe = 0; lobe < 4; lobe++)
        {
            float baseBearing = lobe * 90 * Math.DEG2RAD;

            // Outbound leg
            vector outPt = center;
            outPt[0] = outPt[0] + Math.Cos(baseBearing) * radius;
            outPt[2] = outPt[2] + Math.Sin(baseBearing) * radius;
            outPt[1] = GetGame().GetWorld().GetSurfaceY(outPt[0], outPt[2]);

            AIWaypoint wp1 = SpawnWaypoint(
                "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et", outPt);
            if (wp1) { wp1.SetCompletionRadius(15); group.AddWaypoint(wp1); }

            // Arc point
            float arcBearing = (lobe * 90 + 45) * Math.DEG2RAD;
            vector arcPt = center;
            arcPt[0] = arcPt[0] + Math.Cos(arcBearing) * radius * 0.7;
            arcPt[2] = arcPt[2] + Math.Sin(arcBearing) * radius * 0.7;
            arcPt[1] = GetGame().GetWorld().GetSurfaceY(arcPt[0], arcPt[2]);

            AIWaypoint wp2 = SpawnWaypoint(
                "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et", arcPt);
            if (wp2) { wp2.SetCompletionRadius(15); group.AddWaypoint(wp2); }

            // Return to center
            AIWaypoint wp3 = SpawnWaypoint(
                "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et", center);
            if (wp3) { wp3.SetCompletionRadius(15); group.AddWaypoint(wp3); }
        }

        // Cycle to repeat
        AIWaypoint cycleWp = SpawnWaypoint(
            "{35BD6541CBB8AC08}Prefabs/AI/Waypoints/AIWaypoint_Cycle.et", center);
        if (cycleWp) group.AddWaypoint(cycleWp);

        Print("[GodMode:Squad] " + squadId + " → CLOVERLEAF PATROL (radius " + radius + "m)");
        m_mCurrentOrders.Set(squadId, "patrol_cloverleaf");
        m_mOrderTargets.Set(squadId, center);
        m_mOrderTimestamps.Set(squadId, System.GetTickCount() / 1000.0);
    }

    void OrderPatrolZigzag(string squadId, vector start, vector destination, float lateralOffset)
    {
        SCR_AIGroup group = GetGroup(squadId);
        if (!group) return;
        group.CompleteAllWaypoints();

        if (lateralOffset <= 0) lateralOffset = 80;
        int numZags = 6;

        float dx = (destination[0] - start[0]) / numZags;
        float dz = (destination[2] - start[2]) / numZags;
        float routeLen = Math.Sqrt(dx * dx + dz * dz) * numZags;
        if (routeLen < 1) return;

        float perpX = -dz / (routeLen / numZags);
        float perpZ = dx / (routeLen / numZags);

        for (int i = 0; i <= numZags; i++)
        {
            float baseX = start[0] + dx * i;
            float baseZ = start[2] + dz * i;
            float side = -1;
            if (i % 2 == 0)
                side = 1;

            vector pt = Vector(
                baseX + perpX * lateralOffset * side,
                0,
                baseZ + perpZ * lateralOffset * side
            );
            pt[1] = GetGame().GetWorld().GetSurfaceY(pt[0], pt[2]);

            AIWaypoint wp = SpawnWaypoint(
                "{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et", pt);
            if (wp) { wp.SetCompletionRadius(15); group.AddWaypoint(wp); }
        }

        Print("[GodMode:Squad] " + squadId + " → ZIGZAG PATROL (" + numZags + " zags)");
        m_mCurrentOrders.Set(squadId, "patrol_zigzag");
        m_mOrderTargets.Set(squadId, destination);
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

                // Clean up all tracking maps to prevent memory leaks
                m_mCurrentOrders.Remove(sid);
                m_mOrderTargets.Remove(sid);
                m_mOrderTimestamps.Remove(sid);
                m_mKillsSinceReport.Remove(sid);
                m_mCasualtiesSinceReport.Remove(sid);

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
            string sid = m_mSquads.GetKey(i);

            if (!group)
            {
                toRemove.Insert(sid);
                continue;
            }

            // Filter by faction — determine faction from the group's prefab config
            string groupFaction = GetGroupFaction(group);
            if (groupFaction != faction)
                continue;

            GM_UnitState state = new GM_UnitState();
            state.unit_id = sid;
            state.unit_type = "infantry_squad";
            state.position = group.GetOrigin();
            state.strength = group.GetAgentsCount();
            state.faction = faction;

            // Calculate aggregate health from individual agents instead of binary 1/0
            state.health_pct = CalculateGroupHealth(group);

            // Order execution feedback
            string orderType = "idle";
            m_mCurrentOrders.Find(sid, orderType);
            state.current_order = orderType;

            vector orderTarget = vector.Zero;
            m_mOrderTargets.Find(sid, orderTarget);

            float dist = 0;
            if (orderTarget != vector.Zero)
                dist = vector.Distance(state.position, orderTarget);
            state.distance_to_objective = dist;

            float orderTime = 0;
            m_mOrderTimestamps.Find(sid, orderTime);
            float now = System.GetTickCount() / 1000.0;
            state.time_since_order = 0;
            if (orderTime > 0)
                state.time_since_order = now - orderTime;
            state.time_in_current_order = state.time_since_order;

            // Determine order status using actual health data
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
            m_mKillsSinceReport.Find(sid, kills);
            state.kills = kills;

            int casualties = 0;
            m_mCasualtiesSinceReport.Find(sid, casualties);
            state.casualties = casualties;

            state.ammo_status = "full";  // TODO: query actual ammo when API available
            state.is_suppressed = (state.health_pct < 0.7 && state.order_status == "engaged");

            states.Insert(state);
        }

        foreach (string deadId : toRemove)
        {
            m_mSquads.Remove(deadId);
            // Also clean up orphaned tracking data
            m_mCurrentOrders.Remove(deadId);
            m_mOrderTargets.Remove(deadId);
            m_mOrderTimestamps.Remove(deadId);
            m_mKillsSinceReport.Remove(deadId);
            m_mCasualtiesSinceReport.Remove(deadId);
        }

        return states;
    }

    // Determine faction by checking the group's faction affiliation component
    protected string GetGroupFaction(SCR_AIGroup group)
    {
        if (!group) return "unknown";

        FactionAffiliationComponent factionComp =
            FactionAffiliationComponent.Cast(
                group.FindComponent(FactionAffiliationComponent));
        if (factionComp)
        {
            Faction f = factionComp.GetAffiliatedFaction();
            if (f)
            {
                string fKey = f.GetFactionKey();
                if (fKey == "US") return "us";
                if (fKey == "USSR") return "ussr";
                if (fKey == "FIA") return "fia";
                return fKey;
            }
        }

        // Fallback: infer from prefab name
        if (group.GetPrefabData())
        {
            string prefabName = group.GetPrefabData().GetPrefabName();
            if (prefabName.Contains("USSR") || prefabName.Contains("OPFOR"))
                return "ussr";
            if (prefabName.Contains("US") || prefabName.Contains("BLUFOR"))
                return "us";
            if (prefabName.Contains("FIA") || prefabName.Contains("INDFOR"))
                return "fia";
        }

        return "ussr"; // default assumption for enemy spawns
    }

    // Calculate average health across all agents in the group
    protected float CalculateGroupHealth(SCR_AIGroup group)
    {
        if (!group) return 0.0;

        array<AIAgent> agents = {};
        group.GetAgents(agents);

        if (agents.Count() == 0) return 0.0;

        float totalHealth = 0;
        int validAgents = 0;

        foreach (AIAgent agent : agents)
        {
            if (!agent) continue;
            IEntity agentEntity = agent.GetControlledEntity();
            if (!agentEntity) continue;

            SCR_CharacterDamageManagerComponent dmg =
                SCR_CharacterDamageManagerComponent.Cast(
                    agentEntity.FindComponent(SCR_CharacterDamageManagerComponent));
            if (dmg)
            {
                totalHealth += dmg.GetHealthScaled();
            }
            else
            {
                totalHealth += 1.0; // assume full health if no damage component
            }
            validAgents++;
        }

        if (validAgents == 0) return 0.0;
        return totalHealth / validAgents;
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

    // Find a position on dry land — if the given position is underwater, search outward
    protected vector FindDryLandPosition(vector position)
    {
        float surfaceY = GetGame().GetWorld().GetSurfaceY(position[0], position[2]);

        // If terrain is above 1m elevation, it's land — use as-is
        if (surfaceY >= 1.0)
            return position;

        Print("[GodMode:Squad] Position " + position.ToString(false) + " is underwater (Y=" + surfaceY + ") — searching for land", LogLevel.WARNING);

        // Search in expanding circles for dry land
        for (int ring = 1; ring <= 8; ring++)
        {
            float searchDist = ring * 100; // 100m, 200m, 300m, ...
            for (int dir = 0; dir < 8; dir++)
            {
                float angle = dir * 45 * Math.DEG2RAD;
                vector tryPos = position;
                tryPos[0] = tryPos[0] + Math.Cos(angle) * searchDist;
                tryPos[2] = tryPos[2] + Math.Sin(angle) * searchDist;
                float tryY = GetGame().GetWorld().GetSurfaceY(tryPos[0], tryPos[2]);
                if (tryY >= 1.0)
                {
                    Print("[GodMode:Squad] Found dry land at " + tryPos.ToString(false) + " (Y=" + tryY + ", dist=" + searchDist + "m)");
                    tryPos[1] = tryY;
                    return tryPos;
                }
            }
        }

        // Last resort: try to find nearest player and spawn near them (they're on land)
        array<int> playerIds = {};
        GetGame().GetPlayerManager().GetPlayers(playerIds);
        foreach (int pid : playerIds)
        {
            IEntity playerEnt = GetGame().GetPlayerManager().GetPlayerControlledEntity(pid);
            if (!playerEnt) continue;
            vector playerPos = playerEnt.GetOrigin();
            float pY = GetGame().GetWorld().GetSurfaceY(playerPos[0], playerPos[2]);
            if (pY >= 1.0)
            {
                // Spawn 100-200m from the player in a random direction on land
                for (int a = 0; a < 8; a++)
                {
                    float pAngle = a * 45 * Math.DEG2RAD;
                    vector nearPlayer = playerPos;
                    nearPlayer[0] = nearPlayer[0] + Math.Cos(pAngle) * 150;
                    nearPlayer[2] = nearPlayer[2] + Math.Sin(pAngle) * 150;
                    float npY = GetGame().GetWorld().GetSurfaceY(nearPlayer[0], nearPlayer[2]);
                    if (npY >= 1.0)
                    {
                        Print("[GodMode:Squad] Using position near player as fallback: " + nearPlayer.ToString(false));
                        nearPlayer[1] = npY;
                        return nearPlayer;
                    }
                }
            }
        }

        Print("[GodMode:Squad] WARNING: Could not find dry land — using original position", LogLevel.WARNING);
        return position;
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
        vector waypointPos = position;
        waypointPos[1] = GetGame().GetWorld().GetSurfaceY(position[0], position[2]);
        params.Transform[3] = waypointPos;

        IEntity ent = GetGame().SpawnEntityPrefab(res, GetGame().GetWorld(), params);
        return AIWaypoint.Cast(ent);
    }

    int GetSquadCount() { return m_mSquads.Count(); }
    void SetMaxSquads(int max) { m_iMaxSquads = max; }

    // Public accessor for group lookup (used by GM_GodModeComponent for compound maneuvers)
    SCR_AIGroup GetGroupPublic(string squadId) { return GetGroup(squadId); }

    // ═══════════════════════════════════════════════
    // Prefab Lookup
    // ═══════════════════════════════════════════════
    // NOTE: Verify GUIDs in Workbench Resource Browser for your game version.

    protected ResourceName GetFallbackPrefab(string grpType)
    {
        // Map failed prefabs to known-working alternatives (verified v1.6.0 GUIDs)
        if (grpType.Contains("ussr") || grpType.Contains("opfor"))
            return "{E552DABF3636C2AD}Prefabs/Groups/OPFOR/Group_USSR_RifleSquad.et";
        if (grpType.Contains("us") || grpType.Contains("blufor"))
            return "{DDF3799FA1387848}Prefabs/Groups/BLUFOR/Group_US_RifleSquad.et";
        if (grpType.Contains("fia"))
            return "{C02F81D15E9D8B85}Prefabs/Groups/INDFOR/Group_FIA_RifleSquad.et";
        // Default to USSR rifle squad
        return "{E552DABF3636C2AD}Prefabs/Groups/OPFOR/Group_USSR_RifleSquad.et";
    }

    protected string GetFallbackType(string grpType)
    {
        if (grpType.Contains("ussr") || grpType.Contains("opfor"))
            return "ussr_rifle_squad";
        if (grpType.Contains("us") || grpType.Contains("blufor"))
            return "us_rifle_squad";
        if (grpType.Contains("fia"))
            return "fia_squad";
        return "ussr_rifle_squad";
    }

    protected ResourceName GetGroupPrefab(string grpType)
    {
        switch (grpType)
        {
            // ── US Squads (verified GUIDs v1.6.0) ──
            case "us_rifle_squad":
                return "{DDF3799FA1387848}Prefabs/Groups/BLUFOR/Group_US_RifleSquad.et";
            case "us_fire_team":
                return "{84E5BBAB25EA23E5}Prefabs/Groups/BLUFOR/Group_US_FireTeam.et";
            case "us_light_fire_team":
                return "{FCF7F5DC4F83955C}Prefabs/Groups/BLUFOR/Group_US_LightFireTeam.et";
            case "us_mg_team":
                return "{958039B857396B7B}Prefabs/Groups/BLUFOR/Group_US_MachineGunTeam.et";
            case "us_at_team":
                return "{FAEA8B9E1252F56E}Prefabs/Groups/BLUFOR/Group_US_Team_LAT.et";
            case "us_sniper_team":
                return "{D807C7047E818488}Prefabs/Groups/BLUFOR/Group_US_SniperTeam.et";
            case "us_recon_team":
                return "{F65B7BB712F46FEE}Prefabs/Groups/BLUFOR/Group_US_ReconTeam.et";
            case "us_sentry_team":
                return "{3BF36BDEEB33AEC9}Prefabs/Groups/BLUFOR/Group_US_SentryTeam.et";
            case "us_engineer_squad":
                return "{6B2A6EE5002D200F}Prefabs/Groups/BLUFOR/Group_US_EngineerTeam.et";
            case "us_grenadier_team":
                return "{DE747BC9217D383C}Prefabs/Groups/BLUFOR/Group_US_Team_GL.et";

            // ── USSR Squads (verified GUIDs v1.6.0) ──
            case "ussr_rifle_squad":
                return "{E552DABF3636C2AD}Prefabs/Groups/OPFOR/Group_USSR_RifleSquad.et";
            case "ussr_fire_team":
                return "{657590C1EC9E27D3}Prefabs/Groups/OPFOR/Group_USSR_LightFireTeam.et";
            case "ussr_fire_group":
                return "{30ED11AA4F0D41E5}Prefabs/Groups/OPFOR/Group_USSR_FireGroup.et";
            case "ussr_mg_team":
                return "{A2F75E45C66B1C0A}Prefabs/Groups/OPFOR/Group_USSR_MachineGunTeam.et";
            case "ussr_at_team":
            case "ussr_rpg_team":
                return "{96BAB56E6558788E}Prefabs/Groups/OPFOR/Group_USSR_Team_AT.et";
            case "ussr_lat_team":
                return "{2E59ECDA9ED0B993}Prefabs/Groups/OPFOR/Group_USSR_Team_LAT.et";
            case "ussr_sniper_team":
            case "ussr_recon_team":
                return "{06F0C9675883F18A}Prefabs/Groups/OPFOR/KLMK/Group_USSR_ReconTeam.et";
            case "ussr_sentry_team":
                return "{CB58D90EA14430AD}Prefabs/Groups/OPFOR/Group_USSR_SentryTeam.et";
            case "ussr_grenadier_team":
                return "{43C7A28EEB660FF8}Prefabs/Groups/OPFOR/Group_USSR_Team_GL.et";
            case "ussr_suppress_team":
                return "{1C0502B5729E7231}Prefabs/Groups/OPFOR/Group_USSR_Team_Suppress.et";
            case "ussr_engineer_squad":
                return "{344B4B6F787CFB23}Prefabs/Groups/OPFOR/Group_USSR_EngineerTeam.et";
            case "ussr_maneuver_group":
                return "{1A5F0D93609DA5DA}Prefabs/Groups/OPFOR/Group_USSR_ManeuverGroup.et";

            // ── FIA ──
            case "fia_squad":
                return "{C02F81D15E9D8B85}Prefabs/Groups/INDFOR/Group_FIA_RifleSquad.et";

            // ── Unsupported aliases ──
            case "us_mortar_team":
            case "us_hq_element":
            case "us_medic_team":
            case "us_fist_team":
            case "ussr_mortar_team":
            case "ussr_hq_element":
            case "ussr_medic_team":
            case "fia_recon_cell":
            case "fia_sapper_cell":
            case "fia_mortar_crew":
            case "fia_technical":
                Print("[GodMode:Squad] Unsupported group type: '" + grpType + "' — no verified prefab is configured for this alias", LogLevel.WARNING);
                return "";

            default:
                Print("[GodMode:Squad] Unknown group type: " + grpType, LogLevel.WARNING);
                return "";
        }

        return "";
    }

    // ═══════════════════════════════════════════════
    // Manual Soldier Population (fallback when formation system fails)
    // ═══════════════════════════════════════════════

    protected int GetDesiredSquadSize(string groupType)
    {
        if (groupType.Contains("fire_team") || groupType.Contains("light"))
            return 4;
        if (groupType.Contains("mg_team") || groupType.Contains("at_team") ||
            groupType.Contains("sniper") || groupType.Contains("recon") ||
            groupType.Contains("sentry") || groupType.Contains("grenadier") ||
            groupType.Contains("suppress") || groupType.Contains("lat"))
            return 4;
        if (groupType.Contains("rifle_squad") || groupType.Contains("maneuver") ||
            groupType.Contains("fire_group"))
            return 8;
        return 6;
    }

    protected ResourceName GetSoldierPrefab(string groupType)
    {
        // Determine faction from group type and return appropriate soldier prefab
        if (groupType.Contains("us_"))
            return "{26A9756790131354}Prefabs/Characters/Factions/BLUFOR/US_Army/Character_US_Rifleman.et";

        // Default to USSR
        return "{DCB41B3746FDD1BE}Prefabs/Characters/Factions/OPFOR/USSR_Army/Character_USSR_Rifleman.et";
    }

    protected void ManuallyPopulateGroup(SCR_AIGroup group, string groupType, vector position, int count)
    {
        if (!group || count <= 0) return;

        ResourceName soldierPrefab = GetSoldierPrefab(groupType);
        Resource soldierRes = Resource.Load(soldierPrefab);
        if (!soldierRes || !soldierRes.IsValid())
        {
            Print("[GodMode:Squad] Failed to load soldier prefab: " + soldierPrefab, LogLevel.ERROR);
            return;
        }

        int spawned = 0;
        for (int i = 0; i < count; i++)
        {
            // Spread soldiers in a wider circle to avoid clipping into each other or terrain
            float angle = i * (360.0 / count) * Math.DEG2RAD;
            float spreadRadius = 10.0; // 10m spread to avoid stacking
            vector soldierPos = position;
            soldierPos[0] = soldierPos[0] + Math.Cos(angle) * spreadRadius;
            soldierPos[2] = soldierPos[2] + Math.Sin(angle) * spreadRadius;
            // Add 0.5m height offset to prevent spawning inside terrain
            soldierPos[1] = GetGame().GetWorld().GetSurfaceY(soldierPos[0], soldierPos[2]) + 0.5;

            EntitySpawnParams soldierParams = new EntitySpawnParams();
            soldierParams.TransformMode = ETransformMode.WORLD;
            soldierParams.Transform[3] = soldierPos;

            IEntity soldier = GetGame().SpawnEntityPrefab(soldierRes, GetGame().GetWorld(), soldierParams);
            if (!soldier)
            {
                Print("[GodMode:Squad] Failed to spawn soldier entity " + i, LogLevel.WARNING);
                continue;
            }

            // Check if soldier has health (is actually alive)
            SCR_CharacterDamageManagerComponent dmg =
                SCR_CharacterDamageManagerComponent.Cast(
                    soldier.FindComponent(SCR_CharacterDamageManagerComponent));
            if (dmg)
            {
                float hp = dmg.GetHealthScaled();
                if (hp <= 0)
                {
                    Print("[GodMode:Squad] Soldier spawned dead (hp=" + hp + ") — retrying at offset", LogLevel.WARNING);
                    SCR_EntityHelper.DeleteEntityAndChildren(soldier);
                    // Retry with more offset
                    soldierPos[0] = soldierPos[0] + 3;
                    soldierPos[1] = GetGame().GetWorld().GetSurfaceY(soldierPos[0], soldierPos[2]) + 1.0;
                    soldierParams.Transform[3] = soldierPos;
                    soldier = GetGame().SpawnEntityPrefab(soldierRes, GetGame().GetWorld(), soldierParams);
                    if (!soldier) continue;
                }
            }

            // Add the soldier to the AI group
            AIAgent agent = AIAgent.Cast(soldier.FindComponent(AIAgent));
            if (!agent)
            {
                // Try getting the AIControlComponent which manages the agent
                AIControlComponent aiCtrl = AIControlComponent.Cast(soldier.FindComponent(AIControlComponent));
                if (aiCtrl)
                    agent = aiCtrl.GetAIAgent();
            }

            if (agent)
            {
                group.AddAgent(agent);
                spawned++;
                Print("[GodMode:Squad] Soldier " + i + " alive at " + soldierPos.ToString(false));
            }
            else
            {
                Print("[GodMode:Squad] Soldier has no AI agent — deleting", LogLevel.WARNING);
                SCR_EntityHelper.DeleteEntityAndChildren(soldier);
            }
        }

        Print("[GodMode:Squad] Manually populated: " + spawned + "/" + count + " soldiers added to group");
    }
}
