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
    // Prefab Lookup
    // ═══════════════════════════════════════════════
    // NOTE: GUIDs below are representative. You MUST verify these in the
    // Workbench Resource Browser for your game version. Right-click any
    // prefab → "Copy Resource Path" to get the exact GUID path.

    protected ResourceName GetVehiclePrefab(string type)
    {
        switch (type)
        {
            // ── US Vehicles ──
            case "m998_humvee":
                return "{5D63BFCD0CF5171D}Prefabs/Vehicles/Wheeled/M998/M998_MERDC.et";
            case "m998_humvee_mg":
                return "{F8AD0B2A71D053B0}Prefabs/Vehicles/Wheeled/M998/M998_MG_MERDC.et";
            case "m151_jeep":
                return "{EF93C920FF4B2ED5}Prefabs/Vehicles/Wheeled/M151A2/M151A2.et";
            case "m35_truck":
                return "{3A3B42B17D4CDFE2}Prefabs/Vehicles/Wheeled/M35A2/M35A2.et";

            // ── USSR Vehicles ──
            case "uaz469":
                return "{2B43AF0BE6F3ACED}Prefabs/Vehicles/Wheeled/UAZ469/UAZ469.et";
            case "uaz469_mg":
                return "{3AB3BE7E06DA1839}Prefabs/Vehicles/Wheeled/UAZ469/UAZ469_DShK.et";
            case "btr70":
                return "{1B7E1C1DE3B96050}Prefabs/Vehicles/Wheeled/BTR70/BTR70.et";
            case "ural_truck":
                return "{A6A4292F62F8C82C}Prefabs/Vehicles/Wheeled/Ural4320/Ural4320.et";
            case "bmp1":
                return "{25DFE68DAE77A9AF}Prefabs/Vehicles/Tracked/BMP1/BMP1.et";
            case "t72":
                return "{52DF10B7D361DE80}Prefabs/Vehicles/Tracked/T72/T72A.et";

            // ── Helicopters ──
            case "uh1h":
                return "{E1C9450B2E9A1E38}Prefabs/Vehicles/Helicopters/UH1H/UH1H.et";

            default:
                Print("[GodMode:Vehicle] Unknown vehicle type: " + type, LogLevel.WARNING);
                return "";
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
