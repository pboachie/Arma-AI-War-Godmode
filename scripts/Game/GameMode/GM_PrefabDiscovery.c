// GM_PrefabDiscovery.c — Auto-discovers prefab GUIDs on world load
// Attach GM_PrefabDiscoveryComponentClass to your GameMode entity,
// then hit Play in Workbench. Check the Log Console for results.

[EntityEditorProps(category: "GameScripted/GodMode", description: "Auto-discover prefab GUIDs on world load")]
class GM_PrefabDiscoveryComponentClass : SCR_BaseGameModeComponentClass {}

class GM_PrefabDiscoveryComponent : SCR_BaseGameModeComponent
{
    [Attribute("0", UIWidgets.CheckBox, desc: "Enable automatic prefab discovery when the world starts (Workbench/dev use only)")]
    protected bool m_bAutoRunDiscovery;

    protected int m_iRetryCount = 0;
    protected static const int MAX_RETRIES = 5;
    protected static const int RETRY_DELAY_MS = 5000;

    override void OnWorldPostProcess(World world)
    {
        super.OnWorldPostProcess(world);

        if (!m_bAutoRunDiscovery)
            return;

        // Delay discovery to give entity catalogs time to populate
        GetGame().GetCallqueue().CallLater(RunDiscovery, 10000, false);
    }

    protected void RunDiscovery()
    {
        // Check if catalogs are ready before running
        SCR_EntityCatalogManagerComponent catalogMgr =
            SCR_EntityCatalogManagerComponent.GetInstance();

        if (!catalogMgr && m_iRetryCount < MAX_RETRIES)
        {
            m_iRetryCount++;
            Print("[GodMode:Dev] Catalog manager not ready, retry " + m_iRetryCount + "/" + MAX_RETRIES + "...", LogLevel.WARNING);
            GetGame().GetCallqueue().CallLater(RunDiscovery, RETRY_DELAY_MS, false);
            return;
        }

        // Also check if catalogs actually have entries
        if (catalogMgr)
        {
            SCR_EntityCatalog groupCatalog = catalogMgr.GetEntityCatalogOfType(EEntityCatalogType.GROUP);
            if (groupCatalog)
            {
                array<SCR_EntityCatalogEntry> testEntries = new array<SCR_EntityCatalogEntry>();
                groupCatalog.GetEntityList(testEntries);
                if (testEntries.Count() == 0 && m_iRetryCount < MAX_RETRIES)
                {
                    m_iRetryCount++;
                    Print("[GodMode:Dev] Catalogs empty, retry " + m_iRetryCount + "/" + MAX_RETRIES + "...", LogLevel.WARNING);
                    GetGame().GetCallqueue().CallLater(RunDiscovery, RETRY_DELAY_MS, false);
                    return;
                }
            }
        }

        GM_PrefabDiscovery.DiscoverAll();
    }
}

class GM_PrefabDiscovery
{
    // ─── Discover all group prefabs ───
    static void DiscoverGroups()
    {
        Print("═══════════════════════════════════════════════");
        Print("  PREFAB DISCOVERY — Groups");
        Print("═══════════════════════════════════════════════");

        SCR_EntityCatalogManagerComponent catalogMgr =
            SCR_EntityCatalogManagerComponent.GetInstance();

        if (!catalogMgr)
        {
            Print("ERROR: SCR_EntityCatalogManagerComponent not found");
            return;
        }

        SCR_EntityCatalog catalog = catalogMgr.GetEntityCatalogOfType(
            EEntityCatalogType.GROUP);

        if (!catalog)
        {
            Print("ERROR: Group catalog not found");
            return;
        }

        array<SCR_EntityCatalogEntry> entries = new array<SCR_EntityCatalogEntry>();
        catalog.GetEntityList(entries);

        if (entries.Count() == 0)
        {
            Print("[GodMode:Dev] Group catalog returned 0 entries. Run this helper only in Workbench with editor catalogs loaded.", LogLevel.WARNING);
            return;
        }

        Print("Found " + entries.Count() + " group prefabs:");
        Print("───────────────────────────────────────────────");

        foreach (SCR_EntityCatalogEntry entry : entries)
        {
            Print("  " + entry.GetPrefab());
        }

        Print("═══════════════════════════════════════════════");
    }

    // ─── Discover all vehicle prefabs ───
    static void DiscoverVehicles()
    {
        Print("═══════════════════════════════════════════════");
        Print("  PREFAB DISCOVERY — Vehicles");
        Print("═══════════════════════════════════════════════");

        SCR_EntityCatalogManagerComponent catalogMgr =
            SCR_EntityCatalogManagerComponent.GetInstance();

        if (!catalogMgr)
        {
            Print("ERROR: SCR_EntityCatalogManagerComponent not found");
            return;
        }

        SCR_EntityCatalog catalog = catalogMgr.GetEntityCatalogOfType(
            EEntityCatalogType.VEHICLE);

        if (!catalog)
        {
            Print("ERROR: Vehicle catalog not found");
            return;
        }

        array<SCR_EntityCatalogEntry> entries = new array<SCR_EntityCatalogEntry>();
        catalog.GetEntityList(entries);

        if (entries.Count() == 0)
        {
            Print("[GodMode:Dev] Vehicle catalog returned 0 entries. Run this helper only in Workbench with editor catalogs loaded.", LogLevel.WARNING);
            return;
        }

        Print("Found " + entries.Count() + " vehicle prefabs:");
        Print("───────────────────────────────────────────────");

        foreach (SCR_EntityCatalogEntry entry : entries)
        {
            Print("  " + entry.GetPrefab());
        }

        Print("═══════════════════════════════════════════════");
    }

    // ─── Discover all waypoint prefabs ───
    static void DiscoverWaypoints()
    {
        Print("═══════════════════════════════════════════════");
        Print("  PREFAB DISCOVERY — Waypoints");
        Print("═══════════════════════════════════════════════");
        Print("  Check: Prefabs/AI/Waypoints/ in Resource Browser");
        Print("  Common waypoints:");
        Print("    AIWaypoint_Move.et");
        Print("    AIWaypoint_Attack.et");
        Print("    AIWaypoint_Defend.et");
        Print("    AIWaypoint_Cycle.et");
        Print("    AIWaypoint_SearchAndDestroy.et");
        Print("    AIWaypoint_GetIn.et");
        Print("    AIWaypoint_Wait.et");
        Print("═══════════════════════════════════════════════");
    }

    // ─── Run all discovery ───
    static void DiscoverAll()
    {
        Print("");
        Print("╔═══════════════════════════════════════════════╗");
        Print("║    GOD MODE — PREFAB DISCOVERY TOOL          ║");
        Print("║    Copy these GUIDs into your managers        ║");
        Print("╚═══════════════════════════════════════════════╝");
        Print("");

        DiscoverGroups();
        Print("");
        DiscoverVehicles();
        Print("");
        DiscoverWaypoints();

        Print("");
        Print("Discovery complete. Copy the paths above into:");
        Print("  GM_VehicleManager.c → GetVehiclePrefab()");
        Print("  GM_SquadManager.c   → GetGroupPrefab()");
    }
}
