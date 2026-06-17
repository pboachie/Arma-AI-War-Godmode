// GM_VehicleManager.c — Vehicle Spawning, Crew Loading, and Lifecycle Management
// Handles spawn, track, crew, despawn, and SITREP reporting for all LLM-controlled vehicles.

class GM_VehicleManager
{
    protected ref map<string, IEntity> m_mVehicles = new map<string, IEntity>();
    protected int m_iVehicleCounter = 0;

    // Limits (configurable)
    protected int m_iMaxVehicles = 20;

    // ── Runtime prefab cache (auto-discovered from entity catalog) ──
    protected ref map<string, ResourceName> m_mCatalogVehicles = new map<string, ResourceName>();
    protected bool m_bCatalogReady = false;

    void Init()
    {
        m_mCatalogVehicles = new map<string, ResourceName>();
    }

    protected void EnsureCatalog()
    {
        if (m_bCatalogReady) return;

        SCR_EntityCatalogManagerComponent catalogMgr = SCR_EntityCatalogManagerComponent.GetInstance();
        if (!catalogMgr) return;

        SCR_EntityCatalog vehicleCatalog = catalogMgr.GetEntityCatalogOfType(EEntityCatalogType.VEHICLE);
        if (!vehicleCatalog) return;

        array<SCR_EntityCatalogEntry> entries = new array<SCR_EntityCatalogEntry>();
        vehicleCatalog.GetEntityList(entries);
        if (entries.Count() == 0) return;

        foreach (SCR_EntityCatalogEntry entry : entries)
        {
            ResourceName prefab = entry.GetPrefab();
            string s = "" + prefab;

            if (s.Contains("BTR70"))         m_mCatalogVehicles.Set("btr70", prefab);
            else if (s.Contains("UAZ469_DShK") || s.Contains("UAZ469_Dshk"))
                                             m_mCatalogVehicles.Set("uaz469_mg", prefab);
            else if (s.Contains("UAZ469"))   m_mCatalogVehicles.Set("uaz469", prefab);
            else if (s.Contains("BMP1"))     m_mCatalogVehicles.Set("bmp1", prefab);
            else if (s.Contains("T72"))      m_mCatalogVehicles.Set("t72", prefab);
            else if (s.Contains("Ural4320")) m_mCatalogVehicles.Set("ural_truck", prefab);
            else if (s.Contains("UH1H"))     m_mCatalogVehicles.Set("uh1h", prefab);
            else if (s.Contains("M998") && s.Contains("MG"))
                                             m_mCatalogVehicles.Set("m998_humvee_mg", prefab);
            else if (s.Contains("M998"))     m_mCatalogVehicles.Set("m998_humvee", prefab);
            else if (s.Contains("M151A2"))   m_mCatalogVehicles.Set("m151_jeep", prefab);
            else if (s.Contains("M35A2"))    m_mCatalogVehicles.Set("m35_truck", prefab);
        }

        // Also discover character prefabs for crew
        SCR_EntityCatalog charCatalog = catalogMgr.GetEntityCatalogOfType(EEntityCatalogType.CHARACTER);
        if (charCatalog)
        {
            array<SCR_EntityCatalogEntry> charEntries = new array<SCR_EntityCatalogEntry>();
            charCatalog.GetEntityList(charEntries);
            foreach (SCR_EntityCatalogEntry cEntry : charEntries)
            {
                ResourceName cPrefab = cEntry.GetPrefab();
                string cs = "" + cPrefab;
                if (cs.Contains("USSR") && cs.Contains("Rifleman") && !m_mCatalogVehicles.Contains("_crew_ussr"))
                    m_mCatalogVehicles.Set("_crew_ussr", cPrefab);
                else if (cs.Contains("US") && cs.Contains("Rifleman") && !m_mCatalogVehicles.Contains("_crew_us"))
                    m_mCatalogVehicles.Set("_crew_us", cPrefab);
            }
        }

        m_bCatalogReady = (m_mCatalogVehicles.Count() > 0);
        Print("[GodMode:Vehicle] Catalog discovery: found " + m_mCatalogVehicles.Count() + " entries (vehicles + crew)");
        for (int i = 0; i < m_mCatalogVehicles.Count(); i++)
            Print("[GodMode:Vehicle]   " + m_mCatalogVehicles.GetKey(i) + " = " + m_mCatalogVehicles.GetElement(i));
    }

    protected ResourceName ResolveVehiclePrefab(string vehType)
    {
        EnsureCatalog();

        ResourceName found;
        if (m_mCatalogVehicles.Find(vehType, found))
            return found;

        // Alias resolution
        if (vehType == "btr" || vehType == "apc")
            { if (m_mCatalogVehicles.Find("btr70", found)) return found; }
        if (vehType == "uaz" || vehType == "uaz_mg" || vehType == "uaz_dshk")
        {
            string k = "uaz469";
            if (vehType != "uaz") k = "uaz469_mg";
            if (m_mCatalogVehicles.Find(k, found)) return found;
        }
        if (vehType == "bmp" || vehType == "ifv")
            { if (m_mCatalogVehicles.Find("bmp1", found)) return found; }
        if (vehType == "tank" || vehType == "t72a")
            { if (m_mCatalogVehicles.Find("t72", found)) return found; }
        if (vehType == "humvee" || vehType == "m998")
            { if (m_mCatalogVehicles.Find("m998_humvee", found)) return found; }
        if (vehType == "humvee_mg" || vehType == "m998_mg")
            { if (m_mCatalogVehicles.Find("m998_humvee_mg", found)) return found; }
        if (vehType == "truck" || vehType == "ural" || vehType == "ussr_truck")
            { if (m_mCatalogVehicles.Find("ural_truck", found)) return found; }
        if (vehType == "helicopter" || vehType == "heli" || vehType == "huey" || vehType == "uh1")
            { if (m_mCatalogVehicles.Find("uh1h", found)) return found; }
        if (vehType == "jeep" || vehType == "m151")
            { if (m_mCatalogVehicles.Find("m151_jeep", found)) return found; }
        if (vehType == "us_truck" || vehType == "m35")
            { if (m_mCatalogVehicles.Find("m35_truck", found)) return found; }

        // Fall back to hardcoded
        return GetVehiclePrefab(vehType);
    }

    // ═══════════════════════════════════════════════
    // Spawning
    // ═══════════════════════════════════════════════

    string SpawnVehicle(string vehicleType, vector position)
    {
        // Enforce limits
        if (m_mVehicles.Count() >= m_iMaxVehicles)
        {
            Print("[GodMode:Vehicle] At vehicle limit (" + m_iMaxVehicles + "), cannot spawn", LogLevel.WARNING);
            return "";
        }

        ResourceName prefab = ResolveVehiclePrefab(vehicleType);
        if (prefab == "")
        {
            Print("[GodMode:Vehicle] Unknown type: " + vehicleType, LogLevel.WARNING);
            return "";
        }

        Resource res = Resource.Load(prefab);
        if (!res || !res.IsValid())
        {
            Print("[GodMode:Vehicle] Prefab unavailable: " + prefab + " — trying fallback", LogLevel.WARNING);
            string fbType = GetVehicleFallbackType(vehicleType);
            ResourceName fallback = ResolveVehiclePrefab(fbType);
            if (fallback == "") fallback = GetVehicleFallback(vehicleType);
            if (fallback != "")
            {
                res = Resource.Load(fallback);
                vehicleType = fbType;
            }
            if (!res || !res.IsValid())
            {
                Print("[GodMode:Vehicle] Fallback also failed", LogLevel.ERROR);
                return "";
            }
        }

        // Check if position is underwater — find dry land if so
        float checkY = GetGame().GetWorld().GetSurfaceY(position[0], position[2]);
        if (checkY < 1.0)
        {
            Print("[GodMode:Vehicle] Position is underwater (Y=" + checkY + ") — searching for land", LogLevel.WARNING);
            // Search in 8 directions for dry land
            float searchRadius = 200;
            for (int attempt = 0; attempt < 16; attempt++)
            {
                float angle = attempt * 22.5 * Math.DEG2RAD;
                float dist = searchRadius + (attempt * 50);
                vector tryPos = position;
                tryPos[0] = tryPos[0] + Math.Cos(angle) * dist;
                tryPos[2] = tryPos[2] + Math.Sin(angle) * dist;
                float tryY = GetGame().GetWorld().GetSurfaceY(tryPos[0], tryPos[2]);
                if (tryY >= 1.0)
                {
                    Print("[GodMode:Vehicle] Found land at " + tryPos.ToString(false) + " (Y=" + tryY + ")");
                    position = tryPos;
                    checkY = tryY;
                    break;
                }
            }
        }

        EntitySpawnParams params = new EntitySpawnParams();
        params.TransformMode = ETransformMode.WORLD;
        params.Transform[3] = position;

        // Snap to terrain
        float terrainY = checkY;
        params.Transform[3][1] = terrainY + 0.1; // slight offset to prevent clipping

        IEntity vehicle = GetGame().SpawnEntityPrefab(res, GetGame().GetWorld(), params);
        if (!vehicle)
        {
            Print("[GodMode:Vehicle] Spawn failed for: " + vehicleType, LogLevel.ERROR);
            return "";
        }

        m_iVehicleCounter++;
        string vid = "veh_" + m_iVehicleCounter;
        m_mVehicles.Set(vid, vehicle);

        Print("[GodMode:Vehicle] Spawned " + vehicleType + " → " + vid + " at " + position.ToString(false));
        return vid;
    }

    string SpawnCrewedVehicle(string vehicleType, vector position, string crewFaction = "ussr")
    {
        string vid = SpawnVehicle(vehicleType, position);
        if (vid == "") return "";

        IEntity vehicle = m_mVehicles.Get(vid);
        if (!vehicle) return vid;

        BaseCompartmentManagerComponent compMgr =
            BaseCompartmentManagerComponent.Cast(
                vehicle.FindComponent(BaseCompartmentManagerComponent));
        if (!compMgr)
        {
            Print("[GodMode:Vehicle] No compartment manager on " + vid, LogLevel.WARNING);
            return vid;
        }

        array<BaseCompartmentSlot> compartments = {};
        compMgr.GetCompartments(compartments);

        ResourceName crewRes = GetCrewPrefab(crewFaction);
        Resource crew = Resource.Load(crewRes);
        if (!crew || !crew.IsValid())
        {
            Print("[GodMode:Vehicle] Failed to load crew prefab", LogLevel.ERROR);
            return vid;
        }

        for (int i = 0; i < compartments.Count(); i++)
        {
            EntitySpawnParams crewParams = new EntitySpawnParams();
            crewParams.TransformMode = ETransformMode.WORLD;
            crewParams.Transform[3] = position;

            IEntity crewMember = GetGame().SpawnEntityPrefab(crew, GetGame().GetWorld(), crewParams);
            if (!crewMember) continue;

            SCR_CompartmentAccessComponent access =
                SCR_CompartmentAccessComponent.Cast(
                    crewMember.FindComponent(SCR_CompartmentAccessComponent));
            if (access)
                access.MoveInVehicle(vehicle, i);
        }

        Print("[GodMode:Vehicle] Crewed " + vid + " with " + crewFaction + " (" + compartments.Count() + " slots)");
        return vid;
    }

    // ═══════════════════════════════════════════════
    // Despawning
    // ═══════════════════════════════════════════════

    void DespawnVehicle(string vehicleId)
    {
        IEntity vehicle;
        if (!m_mVehicles.Find(vehicleId, vehicle) || !vehicle)
        {
            Print("[GodMode:Vehicle] Not found: " + vehicleId, LogLevel.WARNING);
            m_mVehicles.Remove(vehicleId);
            return;
        }

        // Eject occupants first
        EjectAllOccupants(vehicle);

        // Delete after brief delay for ejection physics
        GetGame().GetCallqueue().CallLater(DeleteVehicleDeferred, 1500, false, vehicleId);
    }

    protected void EjectAllOccupants(IEntity vehicle)
    {
        BaseCompartmentManagerComponent compMgr =
            BaseCompartmentManagerComponent.Cast(
                vehicle.FindComponent(BaseCompartmentManagerComponent));
        if (!compMgr) return;

        array<BaseCompartmentSlot> compartments = {};
        compMgr.GetCompartments(compartments);

        foreach (BaseCompartmentSlot slot : compartments)
        {
            IEntity occupant = slot.GetOccupant();
            if (!occupant) continue;

            SCR_CompartmentAccessComponent access =
                SCR_CompartmentAccessComponent.Cast(
                    occupant.FindComponent(SCR_CompartmentAccessComponent));
            if (access)
            {
                // TODO: MoveOutVehicle does not exist on SCR_CompartmentAccessComponent.
                // Investigate correct API for ejecting occupants (e.g. CompartmentAccessComponent or direct deletion).
                Print("[GodMode:Vehicle] MoveOutVehicle not available; deleting occupant directly.", LogLevel.WARNING);
                SCR_EntityHelper.DeleteEntityAndChildren(occupant);
            }
        }
    }

    protected void DeleteVehicleDeferred(string vehicleId)
    {
        IEntity vehicle;
        if (!m_mVehicles.Find(vehicleId, vehicle))
        {
            m_mVehicles.Remove(vehicleId);
            return;
        }

        if (vehicle)
            SCR_EntityHelper.DeleteEntityAndChildren(vehicle);

        m_mVehicles.Remove(vehicleId);
        Print("[GodMode:Vehicle] Despawned: " + vehicleId);
    }

    // ═══════════════════════════════════════════════
    // SITREP Data
    // ═══════════════════════════════════════════════

    ref array<ref GM_UnitState> GetVehicleStates()
    {
        ref array<ref GM_UnitState> states = {};

        // Clean up dead references while iterating
        ref array<string> toRemove = {};

        for (int i = 0; i < m_mVehicles.Count(); i++)
        {
            string vid = m_mVehicles.GetKey(i);
            IEntity vehicle = m_mVehicles.GetElement(i);

            if (!vehicle)
            {
                toRemove.Insert(vid);
                continue;
            }

            GM_UnitState state = new GM_UnitState();
            state.unit_id = vid;
            state.unit_type = "vehicle";
            state.position = vehicle.GetOrigin();
            state.strength = 1;
            state.current_order = "idle";

            // Get damage state
            SCR_VehicleDamageManagerComponent dmg =
                SCR_VehicleDamageManagerComponent.Cast(
                    vehicle.FindComponent(SCR_VehicleDamageManagerComponent));
            if (dmg)
                state.health_pct = dmg.GetHealthScaled();
            else
                state.health_pct = 1.0;

            states.Insert(state);
        }

        // Purge dead references
        foreach (string deadId : toRemove)
            m_mVehicles.Remove(deadId);

        return states;
    }

    int GetVehicleCount() { return m_mVehicles.Count(); }
    void SetMaxVehicles(int max) { m_iMaxVehicles = max; }

    // ═══════════════════════════════════════════════
    // Vehicle Orders (requires crew groups)
    // ═══════════════════════════════════════════════

    protected ref map<string, SCR_AIGroup> m_mVehicleGroups = new map<string, SCR_AIGroup>();

    void OrderVehicleMove(string vehicleId, vector destination)
    {
        SCR_AIGroup group;
        if (!m_mVehicleGroups.Find(vehicleId, group) || !group)
        {
            Print("[GodMode:Vehicle] No crew group for " + vehicleId + ", cannot order move", LogLevel.WARNING);
            return;
        }

        EntitySpawnParams params = new EntitySpawnParams();
        params.TransformMode = ETransformMode.WORLD;
        params.Transform[3] = destination;
        params.Transform[3][1] = GetGame().GetWorld().GetSurfaceY(destination[0], destination[2]);

        Resource wpRes = Resource.Load("{750A8D1695BD6998}Prefabs/AI/Waypoints/AIWaypoint_Move.et");
        if (!wpRes || !wpRes.IsValid()) return;

        IEntity wpEnt = GetGame().SpawnEntityPrefab(wpRes, GetGame().GetWorld(), params);
        AIWaypoint wp = AIWaypoint.Cast(wpEnt);
        if (wp)
        {
            wp.SetCompletionRadius(30);
            group.AddWaypoint(wp);
        }

        Print("[GodMode:Vehicle] " + vehicleId + " → MOVE to " + destination.ToString(false));
    }

    void OrderVehicleDefend(string vehicleId, vector position)
    {
        SCR_AIGroup group;
        if (!m_mVehicleGroups.Find(vehicleId, group) || !group) return;

        EntitySpawnParams params = new EntitySpawnParams();
        params.TransformMode = ETransformMode.WORLD;
        params.Transform[3] = position;
        params.Transform[3][1] = GetGame().GetWorld().GetSurfaceY(position[0], position[2]);

        Resource wpRes = Resource.Load("{93291E72AC23930F}Prefabs/AI/Waypoints/AIWaypoint_Defend.et");
        if (!wpRes || !wpRes.IsValid()) return;

        IEntity wpEnt = GetGame().SpawnEntityPrefab(wpRes, GetGame().GetWorld(), params);
        AIWaypoint wp = AIWaypoint.Cast(wpEnt);
        if (wp)
        {
            wp.SetCompletionRadius(50);
            group.AddWaypoint(wp);
        }

        Print("[GodMode:Vehicle] " + vehicleId + " → DEFEND at " + position.ToString(false));
    }

    // Register a crew group for an already-spawned vehicle
    void RegisterVehicleGroup(string vehicleId, SCR_AIGroup group)
    {
        if (group) m_mVehicleGroups.Set(vehicleId, group);
    }

    // Spawn a convoy of vehicles in formation
    void SpawnConvoy(string convoyId, array<string> vehicleTypes, vector startPos,
                      vector destination, float spacing)
    {
        if (spacing <= 0) spacing = 75;

        // Calculate direction vector
        vector dir = destination - startPos;
        float len = dir.Length();
        if (len < 1) return;
        dir = dir / len; // normalize

        for (int i = 0; i < vehicleTypes.Count(); i++)
        {
            // Space vehicles along the route behind the start position
            vector pos = startPos;
            float offset = i * spacing;
            pos[0] = pos[0] - dir[0] * offset;
            pos[2] = pos[2] - dir[2] * offset;

            string vid = SpawnCrewedVehicle(vehicleTypes[i], pos, "ussr");
            if (vid == "") continue;

            // Give the vehicle a move order to the destination
            // Slight delay to ensure crew is loaded
            GetGame().GetCallqueue().CallLater(OrderVehicleMove, 2000 + i * 500, false, vid, destination);
        }

        Print("[GodMode:Vehicle] Convoy " + convoyId + " spawned: " + vehicleTypes.Count() + " vehicles, spacing " + spacing + "m");
    }

    // ═══════════════════════════════════════════════
    // Prefab Lookup
    // ═══════════════════════════════════════════════
    // NOTE: GUIDs below are representative. You MUST verify these in the
    // Workbench Resource Browser for your game version. Right-click any
    // prefab → "Copy Resource Path" to get the exact GUID path.

    protected ResourceName GetVehicleFallback(string vehicleType)
    {
        // Map failed vehicle prefabs to known-working alternatives
        // Heavy armor (T-72, BMP) → BTR-70 (known working APC)
        if (vehicleType == "t72" || vehicleType == "t72a" || vehicleType == "tank" ||
            vehicleType == "bmp1" || vehicleType == "bmp" || vehicleType == "ifv")
            return "{C012BB3488BEA0C2}Prefabs/Vehicles/Wheeled/BTR70/BTR70.et";

        // Light vehicles (UAZ DShK) → plain UAZ
        if (vehicleType == "uaz469_mg" || vehicleType == "uaz_mg" || vehicleType == "uaz_dshk")
            return "{259EE7B78C51B624}Prefabs/Vehicles/Wheeled/UAZ469/UAZ469.et";

        // Default fallback: BTR-70
        return "{C012BB3488BEA0C2}Prefabs/Vehicles/Wheeled/BTR70/BTR70.et";
    }

    protected string GetVehicleFallbackType(string vehicleType)
    {
        if (vehicleType == "uaz469_mg" || vehicleType == "uaz_mg" || vehicleType == "uaz_dshk")
            return "uaz469";
        // BLUFOR vehicles with broken/missing prefabs fall back to m151_jeep, not btr70
        if (vehicleType == "m998_humvee" || vehicleType == "m998_humvee_mg" ||
            vehicleType == "humvee" || vehicleType == "m998" || vehicleType == "m998_mg")
            return "m151_jeep";
        return "btr70";
    }

    protected ResourceName GetVehiclePrefab(string inputType)
    {
        // Note: Enforce Script has no string.Trim() or string.ToLower()
        // LLM must send clean lowercase identifiers
        string vehType = inputType;

        switch (vehType)
        {
            // ── US Vehicles ──
            case "m998_humvee":
            case "humvee":
            case "m998":
                return "{5D63BFCD0CF5171D}Prefabs/Vehicles/Wheeled/M998/M998_MERDC.et";
            case "m998_humvee_mg":
            case "humvee_mg":
            case "m998_mg":
                return "{F8AD0B2A71D053B0}Prefabs/Vehicles/Wheeled/M998/M998_MG_MERDC.et";
            case "m151_jeep":
            case "m151":
            case "jeep":
                return "{EF93C920FF4B2ED5}Prefabs/Vehicles/Wheeled/M151A2/M151A2.et";
            case "m35_truck":
            case "m35":
            case "us_truck":
                return "{3A3B42B17D4CDFE2}Prefabs/Vehicles/Wheeled/M35A2/M35A2.et";

            // ── USSR Vehicles ──
            case "uaz469":
            case "uaz":
                return "{259EE7B78C51B624}Prefabs/Vehicles/Wheeled/UAZ469/UAZ469.et";
            case "uaz469_mg":
            case "uaz_mg":
            case "uaz_dshk":
                return "{3AB3BE7E06DA1839}Prefabs/Vehicles/Wheeled/UAZ469/UAZ469_DShK.et";
            case "btr70":
            case "btr":
            case "apc":
                return "{C012BB3488BEA0C2}Prefabs/Vehicles/Wheeled/BTR70/BTR70.et";
            case "ural_truck":
            case "ural":
            case "ussr_truck":
            case "truck":
                return "{A6A4292F62F8C82C}Prefabs/Vehicles/Wheeled/Ural4320/Ural4320.et";
            case "bmp1":
            case "bmp":
            case "ifv":
                return "{25DFE68DAE77A9AF}Prefabs/Vehicles/Tracked/BMP1/BMP1.et";
            case "t72":
            case "t72a":
            case "tank":
                return "{52DF10B7D361DE80}Prefabs/Vehicles/Tracked/T72/T72A.et";

            // ── Helicopters ──
            case "uh1h":
            case "uh1":
            case "huey":
            case "helicopter":
            case "heli":
                return "{E1C9450B2E9A1E38}Prefabs/Vehicles/Helicopters/UH1H/UH1H.et";

            // ── USSR Extended ──
            case "brdm2":
            case "brdm":
            case "brdm2_atgm":
            case "ural_ammo":
            case "ammo_truck":
            case "ural_fuel":
            case "fuel_truck":
            case "ural_medical":
            case "medical_truck":
            case "zsu_23_4":
            case "zsu":
            case "shilka":
            case "mt_lb":
            case "mtlb":
            case "m113":
            case "m60a3":
            case "m60":
            case "m923_ammo":
            case "us_ammo_truck":
            case "m923_fuel":
            case "us_fuel_truck":
            case "mi8":
            case "hip":
            case "mi24":
            case "hind":
            case "ah1":
            case "cobra":
            case "dshk_tripod":
            case "dshk":
            case "spg9_tripod":
            case "spg9":
            case "ags17_tripod":
            case "ags17":
            case "m2_tripod":
            case "tow_launcher":
            case "tow":
                Print("[GodMode:Vehicle] Unsupported vehicle type: '" + vehType + "' — no verified prefab is configured for this alias", LogLevel.WARNING);
                return "";

            default:
                Print("[GodMode:Vehicle] Unknown vehicle type: '" + vehType + "'", LogLevel.WARNING);
                return "";
        }

        return "";
    }

    protected ResourceName GetCrewPrefab(string faction)
    {
        // Try catalog-discovered character prefabs first
        EnsureCatalog();
        ResourceName found;
        if (faction == "us" && m_mCatalogVehicles.Find("_crew_us", found))
            return found;
        if (m_mCatalogVehicles.Find("_crew_ussr", found))
            return found;

        // Hardcoded fallback
        switch (faction)
        {
            case "us":
                return "{1F5DE3BA6034D382}Prefabs/Characters/Factions/BLUFOR/US_Army/Character_US_Rifleman.et";
            case "ussr":
                return "{DCB41B3746FDD1BE}Prefabs/Characters/Factions/OPFOR/USSR_Army/Character_USSR_Rifleman.et";
            default:
                return "{DCB41B3746FDD1BE}Prefabs/Characters/Factions/OPFOR/USSR_Army/Character_USSR_Rifleman.et";
        }

        return "";
    }
}
