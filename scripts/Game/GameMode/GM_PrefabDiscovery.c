// GM_PrefabDiscovery.c — Run once in Workbench to discover actual prefab GUIDs
// Attach to any entity and call DiscoverAll() from the console, or
// place a call in OnWorldPostProcess to auto-log on load.
//
// USAGE:
//   1. Add this file to your mod
//   2. Load your scenario in Workbench Play mode
//   3. Check the console log for all discovered prefab paths
//   4. Copy the GUIDs into GM_VehicleManager and GM_SquadManager

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

        array<SCR_EntityCatalogEntry> entries = {};
        catalog.GetEntityList(entries);

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

        array<SCR_EntityCatalogEntry> entries = {};
        catalog.GetEntityList(entries);

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
