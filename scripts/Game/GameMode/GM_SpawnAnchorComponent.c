[ComponentEditorProps(category: "GameScripted/GodMode", description: "Editor-placed GodMode spawn anchor")]
class GM_SpawnAnchorComponentClass : ScriptComponentClass {}

class GM_SpawnAnchorComponent : ScriptComponent
{
    protected static ref array<GM_SpawnAnchorComponent> s_aRegistered = new array<GM_SpawnAnchorComponent>();

    [Attribute("", UIWidgets.EditBox, desc: "Anchor name shown to the AI")]
    protected string m_sAnchorName;

    [Attribute("road_junction", UIWidgets.EditBox, desc: "Anchor type: neutral_base | road_junction | roadblock_candidate")]
    protected string m_sAnchorType;

    [Attribute("unassigned", UIWidgets.EditBox, desc: "Control state: neutral | opfor | blufor | unassigned")]
    protected string m_sControlState;

    [Attribute("120", UIWidgets.EditBox, desc: "Recommended tactical radius in meters")]
    protected float m_fTacticalRadius;

    [Attribute("1.0", UIWidgets.EditBox, desc: "Priority from 0.0 to 1.0")]
    protected float m_fPriority;

    [Attribute("", UIWidgets.EditBox, desc: "Optional planning notes")]
    protected string m_sNotes;

    protected IEntity m_pOwnerEntity;

    override void OnPostInit(IEntity owner)
    {
        m_pOwnerEntity = owner;

        if (!Replication.IsServer())
            return;

        if (!s_aRegistered)
            s_aRegistered = new array<GM_SpawnAnchorComponent>();

        if (s_aRegistered.Find(this) == -1)
            s_aRegistered.Insert(this);
    }

    override void OnDelete(IEntity owner)
    {
        if (!s_aRegistered)
            return;

        int idx = s_aRegistered.Find(this);
        if (idx != -1)
            s_aRegistered.Remove(idx);
    }

    static void GetRegistered(notnull out array<GM_SpawnAnchorComponent> outComponents)
    {
        outComponents.Clear();
        if (!s_aRegistered)
            return;

        foreach (GM_SpawnAnchorComponent component : s_aRegistered)
        {
            if (component)
                outComponents.Insert(component);
        }
    }

    string GetAnchorName()
    {
        if (m_sAnchorName != "")
            return m_sAnchorName;

        if (m_pOwnerEntity && m_pOwnerEntity.GetPrefabData())
            return m_pOwnerEntity.GetPrefabData().GetPrefabName();

        return "GodMode Anchor";
    }

    string GetAnchorType()
    {
        if (m_sAnchorType == "")
            return "road_junction";
        return m_sAnchorType;
    }

    string GetControlState()
    {
        if (m_sControlState == "")
            return "unassigned";
        return m_sControlState;
    }

    float GetTacticalRadius()
    {
        if (m_fTacticalRadius <= 0)
            return 120.0;
        return m_fTacticalRadius;
    }

    float GetPriority()
    {
        float prio = m_fPriority;
        if (prio < 0.0) prio = 0.0;
        if (prio > 1.0) prio = 1.0;
        return prio;
    }

    string GetNotes()
    {
        return m_sNotes;
    }

    vector GetAnchorPosition()
    {
        vector pos = vector.Zero;
        if (m_pOwnerEntity)
            pos = m_pOwnerEntity.GetOrigin();

        if (GetGame() && GetGame().GetWorld())
            pos[1] = GetGame().GetWorld().GetSurfaceY(pos[0], pos[2]);

        return pos;
    }
}
