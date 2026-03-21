// GM_VehicleManager.c — Vehicle Spawning, Crew Loading, and Lifecycle Management
// Handles spawn, track, crew, despawn, and SITREP reporting for all LLM-controlled vehicles.

class GM_VehicleManager
{
    protected ref map<string, IEntity> m_mVehicles = new map<string, IEntity>();
    protected int m_iVehicleCounter = 0;

    // Limits (configurable)
    protected int m_iMaxVehicles = 20;

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

        ResourceName prefab = GetVehiclePrefab(vehicleType);
        if (prefab == "")
        {
            Print("[GodMode:Vehicle] Unknown type: " + vehicleType, LogLevel.WARNING);
            return "";
        }

        Resource res = Resource.Load(prefab);
        if (!res || !res.IsValid())
        {
            Print("[GodMode:Vehicle] Failed to load: " + prefab, LogLevel.ERROR);
            return "";
        }

        EntitySpawnParams params = new EntitySpawnParams();
        params.TransformMode = ETransformMode.WORLD;
        params.Transform[3] = position;

        // Snap to terrain
        float terrainY = GetGame().GetWorld().GetSurfaceY(position[0], position[2]);
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

        foreach (BaseCompartmentSlot slot : compartments)
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
                access.MoveInVehicle(vehicle, slot);
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
                access.MoveOutVehicle(-1, vector.Zero);
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

        Resource wpRes = Resource.Load("{22A875E30470BD4F}Prefabs/AI/Waypoints/AIWaypoint_Move.et");
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

        Resource wpRes = Resource.Load("{B3E7B883E4D9E678}Prefabs/AI/Waypoints/AIWaypoint_Defend.et");
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
    void SpawnConvoy(string convoyId, ref array<string> vehicleTypes, vector startPos,
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

    protected ResourceName GetVehiclePrefab(string type)
    {
        // Normalize: lowercase and strip whitespace for resilient LLM output matching
        type = type.Trim();
        type = type.ToLower();

        switch (type)
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
                return "{2B43AF0BE6F3ACED}Prefabs/Vehicles/Wheeled/UAZ469/UAZ469.et";
            case "uaz469_mg":
            case "uaz_mg":
            case "uaz_dshk":
                return "{3AB3BE7E06DA1839}Prefabs/Vehicles/Wheeled/UAZ469/UAZ469_DShK.et";
            case "btr70":
            case "btr":
            case "apc":
                return "{1B7E1C1DE3B96050}Prefabs/Vehicles/Wheeled/BTR70/BTR70.et";
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
                return "{1B7E1C1DE3B96050}Prefabs/Vehicles/Wheeled/BTR70/BTR70.et"; // placeholder — verify GUID
            case "brdm2_atgm":
                return "{1B7E1C1DE3B96050}Prefabs/Vehicles/Wheeled/BTR70/BTR70.et"; // placeholder — verify GUID
            case "ural_ammo":
            case "ammo_truck":
                return "{A6A4292F62F8C82C}Prefabs/Vehicles/Wheeled/Ural4320/Ural4320.et"; // placeholder
            case "ural_fuel":
            case "fuel_truck":
                return "{A6A4292F62F8C82C}Prefabs/Vehicles/Wheeled/Ural4320/Ural4320.et"; // placeholder
            case "ural_medical":
            case "medical_truck":
                return "{A6A4292F62F8C82C}Prefabs/Vehicles/Wheeled/Ural4320/Ural4320.et"; // placeholder
            case "zsu_23_4":
            case "zsu":
            case "shilka":
                return "{25DFE68DAE77A9AF}Prefabs/Vehicles/Tracked/BMP1/BMP1.et"; // placeholder — verify GUID
            case "mt_lb":
            case "mtlb":
                return "{25DFE68DAE77A9AF}Prefabs/Vehicles/Tracked/BMP1/BMP1.et"; // placeholder — verify GUID

            // ── US Extended ──
            case "m113":
                return "{5D63BFCD0CF5171D}Prefabs/Vehicles/Wheeled/M998/M998_MERDC.et"; // placeholder — verify GUID
            case "m60a3":
            case "m60":
                return "{5D63BFCD0CF5171D}Prefabs/Vehicles/Wheeled/M998/M998_MERDC.et"; // placeholder — verify GUID
            case "m923_ammo":
            case "us_ammo_truck":
                return "{3A3B42B17D4CDFE2}Prefabs/Vehicles/Wheeled/M35A2/M35A2.et"; // placeholder
            case "m923_fuel":
            case "us_fuel_truck":
                return "{3A3B42B17D4CDFE2}Prefabs/Vehicles/Wheeled/M35A2/M35A2.et"; // placeholder

            // ── Extended Helicopters ──
            case "mi8":
            case "hip":
                return "{E1C9450B2E9A1E38}Prefabs/Vehicles/Helicopters/UH1H/UH1H.et"; // placeholder — verify GUID
            case "mi24":
            case "hind":
                return "{E1C9450B2E9A1E38}Prefabs/Vehicles/Helicopters/UH1H/UH1H.et"; // placeholder — verify GUID
            case "ah1":
            case "cobra":
                return "{E1C9450B2E9A1E38}Prefabs/Vehicles/Helicopters/UH1H/UH1H.et"; // placeholder — verify GUID

            // ── Static Weapons ──
            case "dshk_tripod":
            case "dshk":
                return "{2B43AF0BE6F3ACED}Prefabs/Vehicles/Wheeled/UAZ469/UAZ469.et"; // placeholder — verify GUID
            case "spg9_tripod":
            case "spg9":
                return "{2B43AF0BE6F3ACED}Prefabs/Vehicles/Wheeled/UAZ469/UAZ469.et"; // placeholder — verify GUID
            case "ags17_tripod":
            case "ags17":
                return "{2B43AF0BE6F3ACED}Prefabs/Vehicles/Wheeled/UAZ469/UAZ469.et"; // placeholder — verify GUID
            case "m2_tripod":
                return "{EF93C920FF4B2ED5}Prefabs/Vehicles/Wheeled/M151A2/M151A2.et"; // placeholder — verify GUID
            case "tow_launcher":
            case "tow":
                return "{EF93C920FF4B2ED5}Prefabs/Vehicles/Wheeled/M151A2/M151A2.et"; // placeholder — verify GUID

            default:
                Print("[GodMode:Vehicle] Unknown vehicle type: '" + type + "', falling back to UAZ469", LogLevel.WARNING);
                return "{2B43AF0BE6F3ACED}Prefabs/Vehicles/Wheeled/UAZ469/UAZ469.et";
        }
    }

    protected ResourceName GetCrewPrefab(string faction)
    {
        switch (faction)
        {
            case "us":
                return "{1F5DE3BA6034D382}Prefabs/Characters/Factions/BLUFOR/US_Army/Character_US_Rifleman.et";
            case "ussr":
                return "{2DD7C5DD855FF20A}Prefabs/Characters/Factions/OPFOR/USSR_Army/Character_USSR_Rifleman.et";
            default:
                return "{2DD7C5DD855FF20A}Prefabs/Characters/Factions/OPFOR/USSR_Army/Character_USSR_Rifleman.et";
        }
    }
}
