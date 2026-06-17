import { useState, useEffect, useCallback, useRef, useMemo } from "react";

const API_BASE = "http://localhost:8000";

// ── Military monospace font via Google Fonts ──
const fontLink = document.createElement("link");
fontLink.href = "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Rajdhani:wght@400;500;600;700&display=swap";
fontLink.rel = "stylesheet";
document.head.appendChild(fontLink);

const POLL_INTERVAL = 3000;

// ── Utility ──
const fmt = (n) => (typeof n === "number" ? n.toFixed(1) : "\u2014");
const fmtTime = (s) => {
  if (!s && s !== 0) return "\u2014";
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const sec = Math.floor(s % 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  return `${m}m ${sec}s`;
};

const ts = (epoch) => {
  if (!epoch) return "\u2014";
  return new Date(epoch * 1000).toLocaleTimeString();
};

const parsePosition = (posStr) => {
  if (!posStr) return null;
  if (Array.isArray(posStr)) return posStr;
  const parts = String(posStr).split(",").map(Number);
  if (parts.length >= 3 && parts.every(n => !isNaN(n))) return parts;
  return null;
};

const pct = (n) => (typeof n === "number" ? `${(n * 100).toFixed(0)}%` : "\u2014");

// =============================================
// SHARED COMPONENTS
// =============================================

function StatusDot({ ok }) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: ok ? "#00ff88" : "#ff4444",
        boxShadow: ok ? "0 0 6px #00ff8866" : "0 0 6px #ff444466",
        marginRight: 8,
      }}
    />
  );
}

function StatCard({ label, value, sub, accent }) {
  return (
    <div
      role="region"
      aria-label={label}
      style={{
        background: "rgba(0,255,136,0.03)",
        border: "1px solid rgba(0,255,136,0.12)",
        padding: "14px 18px",
        flex: 1,
        minWidth: 140,
      }}
    >
      <div style={{ fontSize: 11, color: "#667766", letterSpacing: 1.5, fontFamily: "JetBrains Mono", textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ fontSize: 28, fontWeight: 700, color: accent || "#00ff88", fontFamily: "Rajdhani", marginTop: 2, lineHeight: 1 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: "#556655", fontFamily: "JetBrains Mono", marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

function SectionHeader({ children }) {
  return (
    <h3
      style={{
        fontSize: 11,
        letterSpacing: 2,
        color: "#00ff88",
        fontFamily: "JetBrains Mono",
        textTransform: "uppercase",
        borderBottom: "1px solid rgba(0,255,136,0.15)",
        paddingBottom: 6,
        marginBottom: 12,
        marginTop: 24,
        fontWeight: 500,
      }}
    >
      &#9632; {children}
    </h3>
  );
}

function ProgressBar({ value, max = 1, color = "#00ff88", height = 4, label }) {
  const pctVal = Math.min((value / max) * 100, 100);
  return (
    <div
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-label={label}
      style={{ flex: 1, background: "rgba(0,255,136,0.08)", height, borderRadius: 2, overflow: "hidden" }}
    >
      <div style={{ width: `${pctVal}%`, height: "100%", background: color, borderRadius: 2, transition: "width 0.3s ease" }} />
    </div>
  );
}

// ── Sparkline: renders an SVG mini line chart ──
function Sparkline({ data = [], color = "#00ff88", width = 120, height = 28, label }) {
  if (!data || data.length < 2) {
    return <span style={{ color: "#334433", fontSize: 10, fontFamily: "JetBrains Mono" }}>No data</span>;
  }
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const stepX = width / (data.length - 1);
  const points = data.map((v, i) => `${i * stepX},${height - ((v - min) / range) * (height - 4) - 2}`).join(" ");
  return (
    <svg width={width} height={height} role="img" aria-label={label || "Sparkline chart"} style={{ display: "block" }}>
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

// ── Horizontal bar chart for command distribution ──
function HorizontalBarChart({ data, maxItems = 10 }) {
  if (!data || Object.keys(data).length === 0) {
    return <div style={{ color: "#445544", fontSize: 12, fontFamily: "JetBrains Mono" }}>No command data</div>;
  }
  const sorted = Object.entries(data).sort((a, b) => b[1] - a[1]).slice(0, maxItems);
  const maxVal = sorted[0]?.[1] || 1;
  const typeColors = {
    move: "#00aaff", attack: "#ff4444", defend: "#ffaa00", patrol: "#00ff88",
    spawn_infantry: "#aa88ff", spawn_vehicle: "#ff88aa", set_weather: "#88ddff",
    retreat: "#ffcc00", explosion: "#ff6600", smoke: "#aaaaaa",
    mortar_strike: "#ff8800", air_support: "#ff4488", radio_message: "#00aaff",
    set_ambush: "#44ff88", seek_and_destroy: "#ff6644",
  };

  return (
    <div style={{ fontSize: 11, fontFamily: "JetBrains Mono" }}>
      {sorted.map(([key, count]) => (
        <div key={key} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <span style={{ color: typeColors[key] || "#667766", width: 130, flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{key}</span>
          <ProgressBar value={count} max={maxVal} color={typeColors[key] || "#00ff88"} height={6} label={`${key}: ${count}`} />
          <span style={{ color: "#889988", width: 30, textAlign: "right", flexShrink: 0 }}>{count}</span>
        </div>
      ))}
    </div>
  );
}

// ── Tactic effectiveness table ──
function TacticTable({ tacticData }) {
  if (!tacticData || Object.keys(tacticData).length === 0) {
    return <div style={{ color: "#445544", fontSize: 12, fontFamily: "JetBrains Mono" }}>No tactic data yet</div>;
  }
  const headerStyle = { fontSize: 10, color: "#556655", letterSpacing: 1, textTransform: "uppercase", padding: "4px 8px", borderBottom: "1px solid rgba(0,255,136,0.12)" };
  const cellStyle = { fontSize: 12, fontFamily: "JetBrains Mono", padding: "4px 8px", borderBottom: "1px solid rgba(0,255,136,0.04)" };

  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "JetBrains Mono" }}>
      <thead>
        <tr>
          <th style={{ ...headerStyle, textAlign: "left" }}>Tactic</th>
          <th style={{ ...headerStyle, textAlign: "center" }}>Issued</th>
          <th style={{ ...headerStyle, textAlign: "center" }}>Success</th>
          <th style={{ ...headerStyle, textAlign: "center" }}>Failed</th>
          <th style={{ ...headerStyle, textAlign: "center" }}>Rate</th>
          <th style={{ ...headerStyle, textAlign: "left", width: 80 }}>Bar</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(tacticData).map(([tactic, d]) => {
          const rate = d.success_rate ?? (d.issued > 0 ? d.successful / d.issued : 0);
          const rateColor = rate >= 0.7 ? "#00ff88" : rate >= 0.4 ? "#ffaa00" : "#ff4444";
          return (
            <tr key={tactic}>
              <td style={{ ...cellStyle, color: "#ccddcc" }}>{tactic}</td>
              <td style={{ ...cellStyle, color: "#889988", textAlign: "center" }}>{d.issued}</td>
              <td style={{ ...cellStyle, color: "#00ff88", textAlign: "center" }}>{d.successful}</td>
              <td style={{ ...cellStyle, color: "#ff4444", textAlign: "center" }}>{d.failed}</td>
              <td style={{ ...cellStyle, color: rateColor, textAlign: "center", fontWeight: 600 }}>{(rate * 100).toFixed(0)}%</td>
              <td style={{ ...cellStyle }}>
                <ProgressBar value={rate} max={1} color={rateColor} height={4} label={`${tactic} success rate: ${(rate * 100).toFixed(0)}%`} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function UnitRow({ unit }) {
  const hp = (unit.health_pct || 0) * 100;
  const hpColor = hp > 60 ? "#00ff88" : hp > 30 ? "#ffaa00" : "#ff4444";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "6px 0",
        borderBottom: "1px solid rgba(0,255,136,0.06)",
        fontSize: 12,
        fontFamily: "JetBrains Mono",
      }}
    >
      <span style={{ color: "#00ff88", width: 90, flexShrink: 0 }}>{unit.unit_id}</span>
      <span style={{ color: "#889988", width: 110, flexShrink: 0 }}>{unit.unit_type}</span>
      <span style={{ color: "#667766", width: 60, flexShrink: 0 }}>str:{unit.strength || "?"}</span>
      <ProgressBar value={hp} max={100} color={hpColor} label={`${unit.unit_id} health: ${hp.toFixed(0)}%`} />
      <span style={{ color: hpColor, width: 40, textAlign: "right", flexShrink: 0 }}>{hp.toFixed(0)}%</span>
    </div>
  );
}

function CommandEntry({ cmd, index }) {
  const [expanded, setExpanded] = useState(false);
  const typeColors = {
    move: "#00aaff", attack: "#ff4444", defend: "#ffaa00", patrol: "#00ff88",
    spawn_infantry: "#aa88ff", spawn_vehicle: "#ff88aa", set_weather: "#88ddff",
    retreat: "#ffcc00", explosion: "#ff6600", smoke: "#aaaaaa",
    suppress: "#ff6644", overwatch: "#ffcc00", silent_move: "#446644",
    garrison: "#886644", establish_op: "#668844", fighting_withdrawal: "#ff8800",
    flank_left: "#ff88cc", flank_right: "#ff88cc", breach: "#ff4400",
    patrol_cloverleaf: "#44ff88", patrol_zigzag: "#44ff88",
    hull_down: "#ff8844", cas_run: "#ff4488", convoy_form: "#8888ff",
    artillery_barrage: "#ff6600", mortar_strike: "#ff8800", air_support: "#ff4488",
    building_fire: "#ff4400", environment_mood: "#88ddff",
    radio_comms: "#00aaff", intel_feed: "#aaaa44", faction_comms: "#ff88aa",
  };
  const color = typeColors[cmd.command_type] || "#00ff88";

  return (
    <div
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      style={{
        borderLeft: `2px solid ${color}`,
        padding: "6px 10px",
        marginBottom: 4,
        background: expanded ? "rgba(0,255,136,0.04)" : "transparent",
        cursor: "pointer",
        transition: "background 0.15s",
      }}
      onClick={() => setExpanded(!expanded)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setExpanded(!expanded); } }}
    >
      <div style={{ display: "flex", gap: 10, fontSize: 12, fontFamily: "JetBrains Mono" }}>
        <span style={{ color, fontWeight: 600, width: 140, flexShrink: 0 }}>{cmd.command_type}</span>
        <span style={{ color: "#667766", width: 80, flexShrink: 0 }}>{cmd.unit_id || "\u2014"}</span>
        <span style={{ color: "#556655", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {cmd.narrative || cmd.parameters || ""}
        </span>
      </div>
      {expanded && (
        <div style={{ marginTop: 6, fontSize: 11, color: "#556655", fontFamily: "JetBrains Mono", lineHeight: 1.5 }}>
          {cmd.target_position && <div>Target: <span style={{ color: "#889988" }}>{cmd.target_position}</span></div>}
          {cmd.parameters && <div>Params: <span style={{ color: "#889988" }}>{cmd.parameters}</span></div>}
          {cmd.reasoning && <div>Reasoning: <span style={{ color: "#7799aa" }}>{cmd.reasoning}</span></div>}
          {cmd.narrative && <div>Narrative: <span style={{ color: "#aaaa77" }}>"{cmd.narrative}"</span></div>}
        </div>
      )}
    </div>
  );
}

// =============================================
// TACTICAL MAP
// =============================================

function TacticalMap({ gameState, tacticalIntel }) {
  const canvasRef = useRef(null);
  const [viewport, setViewport] = useState({ centerX: 4096, centerZ: 4096, zoom: 1.0, mapSize: 8192 });
  const [mousePos, setMousePos] = useState(null);

  const worldToScreen = useCallback((wx, wz, canvas) => {
    const scale = canvas.width / (viewport.mapSize / viewport.zoom);
    const ox = viewport.centerX - (viewport.mapSize / viewport.zoom) / 2;
    const oz = viewport.centerZ - (viewport.mapSize / viewport.zoom) / 2;
    return { x: (wx - ox) * scale, y: (wz - oz) * scale };
  }, [viewport]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    // Grid
    ctx.strokeStyle = "rgba(0,255,136,0.06)";
    ctx.lineWidth = 0.5;
    for (let g = 0; g <= viewport.mapSize; g += 500) {
      const s = worldToScreen(g, 0, canvas);
      const e = worldToScreen(g, viewport.mapSize, canvas);
      ctx.beginPath(); ctx.moveTo(s.x, 0); ctx.lineTo(s.x, h); ctx.stroke();
      const s2 = worldToScreen(0, g, canvas);
      ctx.beginPath(); ctx.moveTo(0, s2.y); ctx.lineTo(w, s2.y); ctx.stroke();
    }

    const state = gameState?.state || {};

    // Draw OPFOR units (red diamonds)
    (state.enemy_units || []).forEach(u => {
      const pos = parsePosition(u.position || u.target_position);
      if (!pos) return;
      const { x, y } = worldToScreen(pos[0], pos[2], canvas);
      const sz = 6 + (u.strength || 4);
      const hp = u.health_pct || 1;
      const color = hp > 0.5 ? "#ff4444" : "#ff8844";
      ctx.save(); ctx.translate(x, y);
      ctx.beginPath(); ctx.moveTo(0,-sz); ctx.lineTo(sz,0); ctx.lineTo(0,sz); ctx.lineTo(-sz,0); ctx.closePath();
      ctx.fillStyle = color + "33"; ctx.fill();
      ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.fillStyle = color; ctx.font = "8px JetBrains Mono"; ctx.textAlign = "center";
      ctx.fillText(u.unit_id || "", 0, sz + 10);
      ctx.restore();
    });

    // Draw BLUFOR units (blue rectangles)
    (state.friendly_units || []).forEach(u => {
      const pos = parsePosition(u.position || u.target_position);
      if (!pos) return;
      const { x, y } = worldToScreen(pos[0], pos[2], canvas);
      const sz = 6;
      ctx.save(); ctx.translate(x, y);
      ctx.fillStyle = "rgba(68,136,255,0.2)"; ctx.strokeStyle = "#4488ff"; ctx.lineWidth = 1.5;
      ctx.fillRect(-sz, -sz*0.7, sz*2, sz*1.4); ctx.strokeRect(-sz, -sz*0.7, sz*2, sz*1.4);
      ctx.fillStyle = "#4488ff"; ctx.font = "8px JetBrains Mono"; ctx.textAlign = "center";
      ctx.fillText(u.unit_id || "", 0, sz + 10);
      ctx.restore();
    });

    // Draw players (cyan circles with direction)
    (state.players || []).forEach(p => {
      const pos = p.position || [0,0,0];
      const { x, y } = worldToScreen(pos[0], pos[2], canvas);
      ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(0,255,255,0.2)"; ctx.fill();
      ctx.strokeStyle = "#00ffff"; ctx.lineWidth = 2; ctx.stroke();
      // Heading arrow
      if (p.heading != null) {
        const rad = (p.heading - 90) * Math.PI / 180;
        ctx.beginPath(); ctx.moveTo(x, y);
        ctx.lineTo(x + Math.cos(rad) * 15, y + Math.sin(rad) * 15);
        ctx.strokeStyle = "#00ffff"; ctx.lineWidth = 1.5; ctx.stroke();
      }
      ctx.fillStyle = "#00ffff"; ctx.font = "9px JetBrains Mono"; ctx.textAlign = "center";
      ctx.fillText("P" + (p.player_id || ""), x, y - 12);
    });

    // Draw threat indicators from tactical intel
    if (tacticalIntel?.threat_assessment) {
      tacticalIntel.threat_assessment.forEach(t => {
        if (!t.position || t.stale) return;
        const pos = parsePosition(t.position);
        if (!pos) return;
        const { x, y } = worldToScreen(pos[0], pos[2], canvas);
        const radius = 20 + t.threat_score * 30;
        ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(255,68,68,${0.15 + t.threat_score * 0.3})`;
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.stroke();
        ctx.setLineDash([]);
      });
    }

  }, [gameState, tacticalIntel, viewport, worldToScreen]);

  return (
    <div style={{ position: "relative" }}>
      <canvas ref={canvasRef} width={880} height={500}
        aria-label="Tactical map showing unit positions"
        style={{ background: "#060a06", border: "1px solid rgba(0,255,136,0.12)", width: "100%", cursor: "crosshair" }}
        onWheel={(e) => { e.preventDefault(); setViewport(v => ({ ...v, zoom: Math.max(0.25, Math.min(8, v.zoom * (e.deltaY > 0 ? 0.9 : 1.1))) })); }}
        onMouseMove={(e) => { const r = canvasRef.current.getBoundingClientRect(); setMousePos({ x: e.clientX - r.left, y: e.clientY - r.top }); }}
      />
      <div style={{ position: "absolute", bottom: 8, left: 8, fontSize: 10, fontFamily: "JetBrains Mono", color: "#556655" }}>
        ZOOM: {(viewport.zoom * 100).toFixed(0)}%
      </div>
      {/* Map legend */}
      <div style={{ position: "absolute", top: 8, right: 8, fontSize: 9, fontFamily: "JetBrains Mono", color: "#556655", lineHeight: 1.8 }}>
        <div><span style={{ color: "#00ffff" }}>&#9679;</span> Player</div>
        <div><span style={{ color: "#4488ff" }}>&#9632;</span> BLUFOR</div>
        <div><span style={{ color: "#ff4444" }}>&#9670;</span> OPFOR</div>
        <div><span style={{ color: "rgba(255,68,68,0.4)" }}>&#9711;</span> Threat zone</div>
      </div>
    </div>
  );
}

// =============================================
// AI BEHAVIOR CONTROLS
// =============================================

function AIBehaviorControls({ serverUrl }) {
  const [config, setConfig] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState("");

  useEffect(() => {
    fetch(`${serverUrl}/api/behavior-config`).then(r => r.json()).then(setConfig).catch(() => {});
  }, [serverUrl]);

  const update = (k, v) => { setConfig(c => ({ ...c, [k]: v })); setDirty(true); };
  const save = async () => {
    await fetch(`${serverUrl}/api/behavior-config`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config) });
    setDirty(false); setStatus("Applied"); setTimeout(() => setStatus(""), 2000);
  };

  if (!config) return <div style={{ color: "#445544", fontSize: 12, fontFamily: "JetBrains Mono" }}>Loading behavior config...</div>;

  const sliders = [
    { key: "aggression", label: "AGGRESSION", color: "#ff4444" },
    { key: "force_ratio", label: "FORCE RATIO", color: "#ff8844", max: 3, step: 0.1 },
    { key: "escalation_speed", label: "ESCALATION", color: "#ffaa00" },
    { key: "narrative_intensity", label: "NARRATIVE", color: "#aa88ff" },
    { key: "reinforcement_rate", label: "REINFORCEMENTS", color: "#00aaff" },
    { key: "flanking_tendency", label: "FLANKING", color: "#00ff88" },
    { key: "retreat_threshold", label: "RETREAT THRESHOLD", color: "#ffcc00", max: 0.8 },
    { key: "vehicle_usage", label: "VEHICLE USAGE", color: "#88bbff" },
  ];

  return (
    <div>
      {sliders.map(s => (
        <div key={s.key} style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
            <label htmlFor={`slider-${s.key}`} style={{ fontSize: 10, color: s.color, fontFamily: "JetBrains Mono", letterSpacing: 1 }}>{s.label}</label>
            <span style={{ fontSize: 12, color: s.color, fontFamily: "JetBrains Mono", fontWeight: 700 }}>{((config[s.key] || 0) * 100).toFixed(0)}%</span>
          </div>
          <input id={`slider-${s.key}`} type="range" min={s.min ?? 0} max={s.max ?? 1} step={s.step ?? 0.05} value={config[s.key] || 0}
            onChange={e => update(s.key, parseFloat(e.target.value))} style={{ width: "100%", accentColor: s.color }} />
        </div>
      ))}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        {[
          { label: "Recon Only", p: { aggression: 0.1, force_ratio: 0.3, escalation_speed: 0.1 }},
          { label: "Full Assault", p: { aggression: 0.9, force_ratio: 1.5, escalation_speed: 0.8 }},
          { label: "Guerrilla", p: { aggression: 0.5, force_ratio: 0.4, vehicle_usage: 0.1, flanking_tendency: 0.9 }},
          { label: "Defense", p: { aggression: 0.2, force_ratio: 1.0, escalation_speed: 0.2 }},
        ].map(pr => (
          <button key={pr.label} onClick={() => { setConfig(c => ({ ...c, ...pr.p })); setDirty(true); }}
            style={{ padding: "4px 12px", fontSize: 10, fontFamily: "JetBrains Mono", background: "rgba(0,255,136,0.05)", border: "1px solid rgba(0,255,136,0.15)", color: "#00ff88", cursor: "pointer" }}>
            {pr.label}
          </button>
        ))}
      </div>
      {dirty && <button onClick={save} style={{ padding: "8px 24px", fontFamily: "JetBrains Mono", fontSize: 12, fontWeight: 600, background: "rgba(255,170,0,0.12)", border: "1px solid rgba(255,170,0,0.3)", color: "#ffaa00", cursor: "pointer", letterSpacing: 1, textTransform: "uppercase" }}>APPLY CHANGES</button>}
      {status && <span style={{ marginLeft: 12, fontSize: 12, color: "#00ff88", fontFamily: "JetBrains Mono" }}>{status}</span>}
    </div>
  );
}

// =============================================
// SCENARIO LIBRARY
// =============================================

const SCENARIOS = [
  { id: "ambush_valley", name: "Valley Ambush", desc: "OPFOR L-ambush in valley with MG overwatch", difficulty: "medium", weather: "Foggy", time: 6.0,
    commands: [
      { command_type: "spawn_infantry", unit_id: "new", target_position: "3200,0,4600", parameters: "ussr_mg_team", narrative: "", reasoning: "scenario" },
      { command_type: "spawn_infantry", unit_id: "new", target_position: "3400,0,4650", parameters: "ussr_sniper_team", narrative: "", reasoning: "scenario" },
      { command_type: "spawn_infantry", unit_id: "new", target_position: "3300,0,4800", parameters: "ussr_rifle_squad", narrative: "", reasoning: "scenario" },
    ], narrative: "Soviet forces have established an ambush in the valley." },
  { id: "night_raid", name: "Night Raid", desc: "Coordinated night assault on player position", difficulty: "hard", weather: "Overcast", time: 23.0,
    commands: [
      { command_type: "spawn_infantry", unit_id: "new", target_position: "4000,0,5200", parameters: "ussr_rifle_squad", narrative: "", reasoning: "scenario" },
      { command_type: "spawn_infantry", unit_id: "new", target_position: "3800,0,5000", parameters: "ussr_rifle_squad", narrative: "", reasoning: "scenario" },
      { command_type: "spawn_infantry", unit_id: "new", target_position: "3600,0,5100", parameters: "ussr_mg_team", narrative: "", reasoning: "scenario" },
    ], narrative: "Enemy forces massing for a night assault. Flares spotted northeast." },
  { id: "armor_push", name: "Armored Assault", desc: "T-72 with BMP support advancing on position", difficulty: "extreme", weather: "Overcast", time: 15.0,
    commands: [
      { command_type: "spawn_vehicle", unit_id: "new", target_position: "5000,0,6000", parameters: "t72", narrative: "", reasoning: "scenario" },
      { command_type: "spawn_vehicle", unit_id: "new", target_position: "4900,0,5900", parameters: "bmp1", narrative: "", reasoning: "scenario" },
      { command_type: "spawn_infantry", unit_id: "new", target_position: "4800,0,5850", parameters: "ussr_rifle_squad", narrative: "", reasoning: "scenario" },
    ], narrative: "Armor column approaching from the east. At least one MBT identified." },
  { id: "quiet_patrol", name: "Quiet Patrol", desc: "Light OPFOR presence, good for new players", difficulty: "easy", weather: "Clear", time: 10.0,
    commands: [
      { command_type: "spawn_infantry", unit_id: "new", target_position: "4500,0,5500", parameters: "ussr_fire_team", narrative: "", reasoning: "scenario" },
    ], narrative: "Scattered reports of enemy scouts in the area." },
  { id: "convoy_ambush", name: "Convoy Ambush", desc: "AT teams positioned along supply route", difficulty: "medium", weather: "Clear", time: 14.0,
    commands: [
      { command_type: "spawn_vehicle", unit_id: "new", target_position: "2800,0,3500", parameters: "btr70", narrative: "", reasoning: "scenario" },
      { command_type: "spawn_infantry", unit_id: "new", target_position: "3100,0,3800", parameters: "ussr_at_team", narrative: "", reasoning: "scenario" },
      { command_type: "spawn_infantry", unit_id: "new", target_position: "3300,0,3900", parameters: "ussr_at_team", narrative: "", reasoning: "scenario" },
    ], narrative: "Intel reports enemy AT teams along the main supply route." },
];

function ScenarioLibrary({ serverUrl, fetchData }) {
  const [launching, setLaunching] = useState(null);
  const diffColors = { easy: "#00ff88", medium: "#ffaa00", hard: "#ff6600", extreme: "#ff4444" };

  const launch = async (s) => {
    setLaunching(s.id);
    const envCmds = [];
    if (s.weather) envCmds.push({ command_type: "set_weather", parameters: s.weather, unit_id: "", target_position: "0,0,0", narrative: "", reasoning: "scenario" });
    if (s.time != null) envCmds.push({ command_type: "set_time", parameters: String(s.time), unit_id: "", target_position: "0,0,0", narrative: "", reasoning: "scenario" });
    await fetch(`${serverUrl}/api/manual-command`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ commands: [...envCmds, ...s.commands], narrative_broadcast: s.narrative || "", weather_command: s.weather || "", time_override: s.time ?? -1 }) });
    fetchData(); setLaunching(null);
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
      {SCENARIOS.map(s => (
        <div key={s.id} style={{ background: "rgba(0,255,136,0.02)", border: "1px solid rgba(0,255,136,0.1)", padding: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: "#ccddcc", fontFamily: "Rajdhani" }}>{s.name}</span>
            <span style={{ fontSize: 10, fontFamily: "JetBrains Mono", color: diffColors[s.difficulty], textTransform: "uppercase" }}>{s.difficulty}</span>
          </div>
          <div style={{ fontSize: 11, color: "#667766", fontFamily: "JetBrains Mono", lineHeight: 1.5, marginBottom: 12 }}>{s.desc}</div>
          <div style={{ fontSize: 10, color: "#445544", fontFamily: "JetBrains Mono", marginBottom: 8 }}>
            {s.commands.length} units | {s.weather} | {s.time < 6 || s.time > 20 ? "Night" : "Day"}
          </div>
          <button onClick={() => launch(s)} disabled={launching === s.id}
            style={{ width: "100%", padding: 8, fontFamily: "JetBrains Mono", fontSize: 11, fontWeight: 600, background: "rgba(0,255,136,0.08)", border: "1px solid rgba(0,255,136,0.2)", color: "#00ff88", cursor: "pointer", letterSpacing: 1, textTransform: "uppercase" }}>
            {launching === s.id ? "DEPLOYING..." : "LAUNCH SCENARIO"}
          </button>
        </div>
      ))}
    </div>
  );
}

// =============================================
// MANUAL COMMAND PANEL
// =============================================

function ManualCommandPanel({ onSend }) {
  const [cmdType, setCmdType] = useState("spawn_infantry");
  const [params, setParams] = useState("ussr_rifle_squad");
  const [pos, setPos] = useState("3500,0,4800");
  const [unitId, setUnitId] = useState("new");
  const [narrative, setNarrative] = useState("");
  const [status, setStatus] = useState("");

  const send = async () => {
    const body = {
      commands: [{
        command_type: cmdType,
        unit_id: unitId,
        target_position: pos,
        parameters: params,
        narrative: narrative,
        reasoning: "manual_override",
      }],
      narrative_broadcast: "",
      weather_command: "",
      time_override: -1,
    };
    try {
      const res = await fetch(`${API_BASE}/api/manual-command`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      setStatus(`OK: Queued ${data.command_count} command(s)`);
      if (onSend) onSend();
      setTimeout(() => setStatus(""), 3000);
    } catch (e) {
      setStatus(`ERR: ${e.message}`);
    }
  };

  const inputStyle = {
    background: "rgba(0,255,136,0.05)",
    border: "1px solid rgba(0,255,136,0.15)",
    color: "#ccddcc",
    padding: "6px 10px",
    fontSize: 12,
    fontFamily: "JetBrains Mono",
    outline: "none",
    width: "100%",
    boxSizing: "border-box",
  };

  const labelStyle = { fontSize: 10, color: "#556655", letterSpacing: 1, fontFamily: "JetBrains Mono", marginBottom: 3, textTransform: "uppercase" };

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div>
          <label htmlFor="cmd-type" style={labelStyle}>Command Type</label>
          <select id="cmd-type" value={cmdType} onChange={(e) => setCmdType(e.target.value)} style={inputStyle}>
            <optgroup label="Movement">
              <option value="move">move</option>
              <option value="attack">attack</option>
              <option value="defend">defend</option>
              <option value="patrol">patrol</option>
              <option value="set_ambush">set_ambush</option>
              <option value="seek_and_destroy">seek_and_destroy</option>
              <option value="retreat">retreat</option>
            </optgroup>
            <optgroup label="Spawning">
              <option value="spawn_infantry">spawn_infantry</option>
              <option value="spawn_vehicle">spawn_vehicle</option>
              <option value="spawn_reinforcements">spawn_reinforcements</option>
            </optgroup>
            <optgroup label="Environment">
              <option value="set_weather">set_weather</option>
              <option value="set_time">set_time</option>
              <option value="set_wind">set_wind</option>
              <option value="thunder_near">thunder_near</option>
            </optgroup>
            <optgroup label="Effects">
              <option value="explosion">explosion</option>
              <option value="smoke">smoke</option>
              <option value="flare">flare</option>
            </optgroup>
            <optgroup label="Cleanup">
              <option value="cleanup_area">cleanup_area</option>
              <option value="cleanup_dead">cleanup_dead</option>
              <option value="despawn_unit">despawn_unit</option>
            </optgroup>
            <optgroup label="Narrative">
              <option value="radio_message">radio_message</option>
              <option value="objective_create">objective_create</option>
              <option value="objective_complete">objective_complete</option>
            </optgroup>
            <optgroup label="Advanced Tactical">
              <option value="suppress">suppress</option>
              <option value="overwatch">overwatch</option>
              <option value="silent_move">silent_move</option>
              <option value="garrison">garrison</option>
              <option value="establish_op">establish_op</option>
              <option value="fighting_withdrawal">fighting_withdrawal</option>
              <option value="flank_left">flank_left</option>
              <option value="flank_right">flank_right</option>
              <option value="breach">breach</option>
              <option value="patrol_cloverleaf">patrol_cloverleaf</option>
              <option value="patrol_zigzag">patrol_zigzag</option>
              <option value="bounding_overwatch">bounding_overwatch</option>
            </optgroup>
            <optgroup label="Vehicle Tactical">
              <option value="hull_down">hull_down</option>
              <option value="overwatch_position">overwatch_position</option>
              <option value="dismount_assault">dismount_assault</option>
              <option value="cas_run">cas_run</option>
              <option value="convoy_form">convoy_form</option>
            </optgroup>
            <optgroup label="Fire Support">
              <option value="mortar_strike">mortar_strike</option>
              <option value="artillery_barrage">artillery_barrage</option>
              <option value="air_support">air_support</option>
            </optgroup>
            <optgroup label="Advanced Environment">
              <option value="set_weather_progression">set_weather_progression</option>
              <option value="set_tactical_lighting">set_tactical_lighting</option>
              <option value="environment_mood">environment_mood</option>
              <option value="set_ambient_intensity">set_ambient_intensity</option>
              <option value="building_fire">building_fire</option>
              <option value="distant_combat_ambience">distant_combat_ambience</option>
            </optgroup>
            <optgroup label="Advanced Narrative">
              <option value="radio_comms">radio_comms</option>
              <option value="intel_feed">intel_feed</option>
              <option value="faction_comms">faction_comms</option>
              <option value="player_acknowledgment">player_acknowledgment</option>
            </optgroup>
            <optgroup label="Fortification">
              <option value="establish_checkpoint">establish_checkpoint</option>
              <option value="lay_mines">lay_mines</option>
              <option value="set_roadblock">set_roadblock</option>
            </optgroup>
          </select>
        </div>
        <div>
          <label htmlFor="cmd-params" style={labelStyle}>Parameters</label>
          <input id="cmd-params" value={params} onChange={(e) => setParams(e.target.value)} style={inputStyle} placeholder="e.g. ussr_rifle_squad" />
        </div>
        <div>
          <label htmlFor="cmd-pos" style={labelStyle}>Target Position (x,y,z)</label>
          <input id="cmd-pos" value={pos} onChange={(e) => setPos(e.target.value)} style={inputStyle} placeholder="3500,0,4800" />
        </div>
        <div>
          <label htmlFor="cmd-unit" style={labelStyle}>Unit ID</label>
          <input id="cmd-unit" value={unitId} onChange={(e) => setUnitId(e.target.value)} style={inputStyle} placeholder="squad_1 or new" />
        </div>
        <div style={{ gridColumn: "span 2" }}>
          <label htmlFor="cmd-narrative" style={labelStyle}>Narrative (optional)</label>
          <input id="cmd-narrative" value={narrative} onChange={(e) => setNarrative(e.target.value)} style={inputStyle} placeholder="Flavor text shown to players" />
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10 }}>
        <button
          onClick={send}
          style={{
            background: "rgba(0,255,136,0.12)",
            border: "1px solid rgba(0,255,136,0.3)",
            color: "#00ff88",
            padding: "8px 24px",
            fontFamily: "JetBrains Mono",
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
            letterSpacing: 1,
            textTransform: "uppercase",
          }}
        >
          Send Command
        </button>
        {status && <span style={{ fontSize: 12, color: status.startsWith("OK") ? "#00ff88" : "#ff4444", fontFamily: "JetBrains Mono" }}>{status}</span>}
      </div>
    </div>
  );
}

// =============================================
// TACTICAL INTEL PANEL (NEW)
// =============================================

function TacticalIntelPanel({ tacticalIntel }) {
  if (!tacticalIntel || tacticalIntel.status === "no_data") {
    return <div style={{ color: "#445544", fontSize: 12, fontFamily: "JetBrains Mono" }}>Awaiting first SITREP for tactical intelligence...</div>;
  }

  const fd = tacticalIntel.force_disposition || {};
  const ad = tacticalIntel.adaptive_difficulty || {};
  const env = tacticalIntel.environment || {};
  const readiness = tacticalIntel.readiness || {};
  const threats = tacticalIntel.threat_assessment || [];
  const profiles = tacticalIntel.player_profiles || {};

  const diffColor = ad.modifier > 1.3 ? "#ff4444" : ad.modifier > 1.0 ? "#ffaa00" : ad.modifier < 0.7 ? "#4488ff" : "#00ff88";
  const killRatio = ad.player_kills && ad.ai_kills ? (ad.player_kills / Math.max(ad.ai_kills, 1)).toFixed(2) : "\u2014";

  return (
    <div>
      {/* Force Disposition */}
      <SectionHeader>Force Disposition</SectionHeader>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <StatCard label="AI Squads" value={fd.ai_squads ?? "\u2014"} sub={`Total strength: ${fd.ai_total_strength ?? "\u2014"}`} accent="#ff8844" />
        <StatCard label="Players" value={fd.player_count ?? "\u2014"} sub={`Contacts: ${fd.known_contacts ?? 0}`} accent="#00ffff" />
        <StatCard label="Force Ratio" value={`${fd.force_ratio ?? "\u2014"}:1`} sub="AI-to-player" accent="#ffaa00" />
        <StatCard label="AI Avg Health" value={pct(fd.ai_avg_health)} accent={fd.ai_avg_health > 0.6 ? "#00ff88" : "#ff8844"} />
      </div>

      {/* Adaptive Difficulty */}
      <SectionHeader>Adaptive Difficulty</SectionHeader>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <StatCard label="Modifier" value={`x${ad.modifier ?? "\u2014"}`} sub={ad.assessment || ""} accent={diffColor} />
        <StatCard label="Player Kills" value={ad.player_kills ?? 0} accent="#00ff88" />
        <StatCard label="AI Kills" value={ad.ai_kills ?? 0} accent="#ff4444" />
        <StatCard label="Kill Ratio" value={killRatio} sub="player:AI" accent={killRatio > 1 ? "#00ff88" : "#ff8844"} />
      </div>

      {/* Environment Intel */}
      <SectionHeader>Environment Assessment</SectionHeader>
      <div style={{ fontSize: 12, fontFamily: "JetBrains Mono", color: "#889988", lineHeight: 1.8, display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 24px" }}>
        <div>Visibility: <span style={{ color: env.visibility_label === "excellent" ? "#00ff88" : env.visibility_label === "poor" ? "#ff4444" : "#ffaa00" }}>{env.visibility_label || "\u2014"} ({pct(env.visibility)})</span></div>
        <div>Night: <span style={{ color: env.is_night ? "#6666aa" : "#00ff88" }}>{env.is_night ? "Yes" : "No"}</span></div>
        <div>Dawn/Dusk: <span style={{ color: env.is_dusk_dawn ? "#ff8844" : "#667766" }}>{env.is_dusk_dawn ? "Yes" : "No"}</span></div>
        <div>Wind affects smoke: <span style={{ color: env.wind_affects_smoke ? "#ffaa00" : "#667766" }}>{env.wind_affects_smoke ? "Yes" : "No"}</span></div>
        {env.weather_tactical && <div style={{ gridColumn: "span 2" }}>Tactical weather: <span style={{ color: "#88ddff" }}>{env.weather_tactical}</span></div>}
      </div>

      {/* Force Readiness */}
      <SectionHeader>Force Readiness</SectionHeader>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <StatCard label="Combat Ready" value={readiness.combat_ready ?? "\u2014"} accent="#00ff88" />
        <StatCard label="Damaged" value={readiness.damaged ?? "\u2014"} accent="#ffaa00" />
        <StatCard label="Effectiveness" value={pct(readiness.combat_effectiveness)} accent={readiness.combat_effectiveness > 0.6 ? "#00ff88" : "#ff4444"} />
      </div>
      {readiness.needs_reinforcement && (
        <div style={{ marginTop: 8, padding: "8px 12px", background: "rgba(255,68,68,0.06)", border: "1px solid rgba(255,68,68,0.2)", fontSize: 11, fontFamily: "JetBrains Mono", color: "#ff8844" }}>
          ALERT: Force strength is below threshold. Reinforcements recommended.
        </div>
      )}

      {/* Threat Assessment */}
      {threats.length > 0 && (
        <>
          <SectionHeader>Threat Assessment ({threats.length})</SectionHeader>
          <div style={{ fontSize: 11, fontFamily: "JetBrains Mono" }}>
            {threats.slice(0, 8).map((t, i) => {
              const threatColor = t.threat_score > 0.7 ? "#ff4444" : t.threat_score > 0.3 ? "#ffaa00" : "#667766";
              return (
                <div key={i} style={{ display: "flex", gap: 12, padding: "4px 0", borderBottom: "1px solid rgba(0,255,136,0.04)", alignItems: "center" }}>
                  <span style={{ color: threatColor, width: 80, flexShrink: 0 }}>{t.contact_id || `Contact ${i+1}`}</span>
                  <span style={{ color: "#889988", width: 100, flexShrink: 0 }}>{t.unit_type}</span>
                  <ProgressBar value={t.threat_score} max={1} color={threatColor} height={4} label={`Threat score: ${t.threat_score}`} />
                  <span style={{ color: threatColor, width: 40, textAlign: "right", flexShrink: 0 }}>{(t.threat_score * 100).toFixed(0)}%</span>
                  {t.stale && <span style={{ color: "#556655", fontSize: 9, flexShrink: 0 }}>[STALE]</span>}
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Player Profiles */}
      {Object.keys(profiles).length > 0 && (
        <>
          <SectionHeader>Player Profiles</SectionHeader>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 8 }}>
            {Object.entries(profiles).map(([pid, profile]) => (
              <div key={pid} style={{ background: "rgba(0,255,136,0.02)", border: "1px solid rgba(0,255,136,0.08)", padding: 10, fontSize: 11, fontFamily: "JetBrains Mono" }}>
                <div style={{ color: "#00ffff", fontWeight: 600, marginBottom: 4 }}>Player {pid}</div>
                {profile.playstyle && <div style={{ color: "#889988" }}>Style: <span style={{ color: "#ccddcc" }}>{profile.playstyle}</span></div>}
                {profile.threat_level && <div style={{ color: "#889988" }}>Threat: <span style={{ color: profile.threat_level === "high" ? "#ff4444" : "#ffaa00" }}>{profile.threat_level}</span></div>}
                {profile.engagement_range && <div style={{ color: "#889988" }}>Range: <span style={{ color: "#ccddcc" }}>{profile.engagement_range}</span></div>}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// =============================================
// BATTLE PLAN PANEL (NEW)
// =============================================

function BattlePlanPanel({ battlePlan }) {
  if (!battlePlan || battlePlan.status === "no_plan") {
    return <div style={{ color: "#445544", fontSize: 12, fontFamily: "JetBrains Mono" }}>No active battle plan. Awaiting first engagement...</div>;
  }

  const phaseColors = {
    reconnaissance: "#00aaff",
    buildup: "#aa88ff",
    probing: "#88ddff",
    assault: "#ff4444",
    exploitation: "#ff8844",
    defense: "#ffaa00",
    defensive_withdrawal: "#ff6600",
  };

  const phaseColor = phaseColors[battlePlan.phase] || "#00ff88";

  return (
    <div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <StatCard label="Phase" value={battlePlan.phase?.replace(/_/g, " ").toUpperCase() || "\u2014"} accent={phaseColor} />
        <StatCard label="Turn" value={battlePlan.turn ?? 0} accent="#00aaff" />
        <StatCard label="Active Ops" value={battlePlan.active_operations?.length ?? 0} accent="#aa88ff" />
      </div>

      {/* Active Operations */}
      {battlePlan.active_operations?.length > 0 && (
        <>
          <SectionHeader>Active Operations</SectionHeader>
          <div style={{ fontSize: 11, fontFamily: "JetBrains Mono" }}>
            {battlePlan.active_operations.map((op, i) => (
              <div key={i} style={{ display: "flex", gap: 12, padding: "4px 0", borderBottom: "1px solid rgba(0,255,136,0.04)" }}>
                <span style={{ color: "#ffaa00", width: 120, flexShrink: 0, fontWeight: 600 }}>{op.type}</span>
                <span style={{ color: "#889988", width: 80, flexShrink: 0 }}>{op.unit || "\u2014"}</span>
                <span style={{ color: "#667766", width: 120, flexShrink: 0 }}>{op.target || "\u2014"}</span>
                <span style={{ color: "#556655", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{op.reasoning}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Lessons Learned */}
      {battlePlan.lessons_learned?.length > 0 && (
        <>
          <SectionHeader>Lessons Learned</SectionHeader>
          <div style={{ fontSize: 11, fontFamily: "JetBrains Mono", color: "#889988", lineHeight: 1.6 }}>
            {battlePlan.lessons_learned.map((lesson, i) => (
              <div key={i} style={{ padding: "2px 0" }}>
                <span style={{ color: "#556655", marginRight: 8 }}>*</span>{lesson}
              </div>
            ))}
          </div>
        </>
      )}

      {/* Briefing */}
      {battlePlan.briefing && (
        <>
          <SectionHeader>Briefing</SectionHeader>
          <pre style={{ fontSize: 11, fontFamily: "JetBrains Mono", color: "#889988", lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0 }}>
            {battlePlan.briefing}
          </pre>
        </>
      )}
    </div>
  );
}

// =============================================
// ANALYTICS PANEL (NEW) - Post-battle analysis, command patterns, response times
// =============================================

function AnalyticsPanel({ statsData, commandLog }) {
  // Compute command type distribution from log
  const commandDistribution = useMemo(() => {
    const dist = {};
    (commandLog || []).forEach(entry => {
      const cmds = entry.commands?.commands || [];
      cmds.forEach(cmd => {
        const t = cmd.command_type || "unknown";
        dist[t] = (dist[t] || 0) + 1;
      });
    });
    return dist;
  }, [commandLog]);

  // Extract difficulty trend from command log
  const difficultyTrend = useMemo(() => {
    return (commandLog || [])
      .filter(e => e.difficulty != null)
      .slice(-20)
      .map(e => e.difficulty);
  }, [commandLog]);

  // Response time data
  const responseTimes = statsData?.recent_response_times_ms || [];

  // Tactic performance from stats
  const tacticPerf = useMemo(() => {
    if (!statsData?.tactic_performance) return {};
    // Flatten all session tactic data
    const merged = {};
    Object.values(statsData.tactic_performance).forEach(sessionData => {
      const outcomes = sessionData.tactic_outcomes || {};
      Object.entries(outcomes).forEach(([tactic, data]) => {
        if (!merged[tactic]) merged[tactic] = { issued: 0, successful: 0, failed: 0, success_rate: 0 };
        merged[tactic].issued += data.issued;
        merged[tactic].successful += data.successful;
        merged[tactic].failed += data.failed;
      });
    });
    // Recalculate rates
    Object.values(merged).forEach(d => {
      d.success_rate = d.issued > 0 ? d.successful / d.issued : 0;
    });
    return merged;
  }, [statsData]);

  if (!statsData) {
    return <div style={{ color: "#445544", fontSize: 12, fontFamily: "JetBrains Mono" }}>Loading analytics...</div>;
  }

  const latency = statsData.latency || {};

  return (
    <div>
      {/* Server Performance */}
      <SectionHeader>Server Performance</SectionHeader>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <StatCard label="Uptime" value={fmtTime(statsData.uptime_seconds)} />
        <StatCard label="Total Requests" value={statsData.total_requests ?? 0} />
        <StatCard label="Total Commands" value={statsData.total_commands_issued ?? 0} sub={`Avg: ${statsData.avg_cmds ?? "\u2014"}/req`} />
        <StatCard label="Error Rate" value={`${statsData.error_rate ?? 0}%`} accent={(statsData.error_rate ?? 0) > 5 ? "#ff4444" : "#00ff88"} />
      </div>

      {/* LLM Health */}
      <SectionHeader>LLM Health</SectionHeader>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <StatCard label="In-Flight" value={`${statsData.llm_inflight ?? 0}/${statsData.llm_max_concurrent ?? "\u2014"}`} accent="#00aaff" />
        <StatCard label="Circuit Breaker" value={statsData.llm_circuit_status?.toUpperCase() || "CLOSED"} accent={statsData.llm_circuit_status === "closed" ? "#00ff88" : "#ff4444"} />
        <StatCard label="Empty Response" value={`${statsData.empty_response_rate ?? 0}%`} accent={(statsData.empty_response_rate ?? 0) > 5 ? "#ff4444" : "#00ff88"} />
        <StatCard label="Parse Failures" value={`${statsData.parse_failure_rate ?? 0}%`} accent={(statsData.parse_failure_rate ?? 0) > 5 ? "#ff4444" : "#00ff88"} />
      </div>

      {/* Token Usage */}
      <SectionHeader>Token Usage</SectionHeader>
      <div style={{ fontSize: 12, fontFamily: "JetBrains Mono", color: "#889988", lineHeight: 1.8 }}>
        <div>Input tokens: <span style={{ color: "#ccddcc" }}>{statsData.total_input_tokens?.toLocaleString() ?? "\u2014"}</span></div>
        <div>Output tokens: <span style={{ color: "#ccddcc" }}>{statsData.total_output_tokens?.toLocaleString() ?? "\u2014"}</span></div>
        <div>Total: <span style={{ color: "#00ff88" }}>{((statsData.total_input_tokens || 0) + (statsData.total_output_tokens || 0)).toLocaleString()}</span></div>
      </div>

      {/* Response Time Trend */}
      <SectionHeader>Response Time Trend</SectionHeader>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 8 }}>
        <Sparkline data={responseTimes} color="#00aaff" width={200} height={32} label="Response time trend (ms)" />
        <div style={{ fontSize: 11, fontFamily: "JetBrains Mono", color: "#889988" }}>
          {responseTimes.length > 0 ? (
            <>
              <span>Last: <span style={{ color: "#00aaff" }}>{responseTimes[responseTimes.length - 1]}ms</span></span>
              <span style={{ marginLeft: 12 }}>Avg: <span style={{ color: "#00aaff" }}>{Math.round(responseTimes.reduce((a, b) => a + b, 0) / responseTimes.length)}ms</span></span>
            </>
          ) : "No response time data"}
        </div>
      </div>
      {latency.count > 0 && (
        <div style={{ fontSize: 11, fontFamily: "JetBrains Mono", color: "#667766" }}>
          P95: <span style={{ color: "#ffaa00" }}>{latency.p95_ms}ms</span> | Max: <span style={{ color: "#ff4444" }}>{latency.max_ms}ms</span> | Samples: {latency.count}
        </div>
      )}

      {/* Difficulty Over Time */}
      <SectionHeader>Difficulty Over Time</SectionHeader>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 8 }}>
        <Sparkline data={difficultyTrend} color="#ffaa00" width={200} height={32} label="Difficulty modifier trend" />
        <div style={{ fontSize: 11, fontFamily: "JetBrains Mono", color: "#889988" }}>
          {difficultyTrend.length > 0 ? (
            <>
              <span>Current: <span style={{ color: "#ffaa00" }}>x{difficultyTrend[difficultyTrend.length - 1]?.toFixed(2)}</span></span>
              <span style={{ marginLeft: 12 }}>Range: <span style={{ color: "#667766" }}>x{Math.min(...difficultyTrend).toFixed(2)} - x{Math.max(...difficultyTrend).toFixed(2)}</span></span>
            </>
          ) : "No difficulty data"}
        </div>
      </div>
      <div style={{ fontSize: 11, fontFamily: "JetBrains Mono", color: "#667766" }}>
        Current: <span style={{ color: "#ffaa00" }}>x{statsData.difficulty?.toFixed(2) ?? "\u2014"}</span>
        {" | "}Player kills: <span style={{ color: "#00ff88" }}>{statsData.player_kills ?? 0}</span>
        {" | "}AI kills: <span style={{ color: "#ff4444" }}>{statsData.ai_kills ?? 0}</span>
        {" | "}K/D ratio: <span style={{ color: "#ccddcc" }}>{statsData.player_kills && statsData.ai_kills ? (statsData.player_kills / Math.max(statsData.ai_kills, 1)).toFixed(2) : "\u2014"}</span>
      </div>

      {/* Command Pattern Distribution */}
      <SectionHeader>Command Distribution</SectionHeader>
      <HorizontalBarChart data={commandDistribution} maxItems={12} />

      {/* Tactic Effectiveness */}
      <SectionHeader>Tactic Effectiveness</SectionHeader>
      <TacticTable tacticData={tacticPerf} />
    </div>
  );
}

// =============================================
// MAIN DASHBOARD
// =============================================

export default function Dashboard() {
  const [health, setHealth] = useState(null);
  const [gameState, setGameState] = useState(null);
  const [commandLog, setCommandLog] = useState([]);
  const [connected, setConnected] = useState(false);
  const [tab, setTab] = useState("overview");
  const [serverUrl, setServerUrl] = useState(API_BASE);
  const [tacticalIntel, setTacticalIntel] = useState(null);
  const [battlePlan, setBattlePlan] = useState(null);
  const [statsData, setStatsData] = useState(null);
  const logRef = useRef(null);

  const fetchData = useCallback(async () => {
    try {
      const [hRes, gRes, cRes, tiRes, bpRes, sRes] = await Promise.all([
        fetch(`${serverUrl}/health`).then((r) => r.json()),
        fetch(`${serverUrl}/api/game-state`).then((r) => r.json()),
        fetch(`${serverUrl}/api/command-log`).then((r) => r.json()),
        fetch(`${serverUrl}/api/tactical-intel`).then((r) => r.json()).catch(() => null),
        fetch(`${serverUrl}/api/battle-plan`).then((r) => r.json()).catch(() => null),
        fetch(`${serverUrl}/stats`).then((r) => r.json()).catch(() => null),
      ]);
      setHealth(hRes);
      setGameState(gRes);
      setCommandLog(cRes.commands || []);
      setTacticalIntel(tiRes);
      setBattlePlan(bpRes);
      setStatsData(sRes);
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, [serverUrl]);

  useEffect(() => {
    fetchData();
    const iv = setInterval(fetchData, POLL_INTERVAL);
    return () => clearInterval(iv);
  }, [fetchData]);

  const state = gameState?.state || {};
  const friendlyUnits = state.friendly_units || [];
  const enemyUnits = state.enemy_units || [];
  const players = state.players || [];
  const events = state.recent_events || [];
  const allCommands = commandLog.flatMap((c) => (c.commands?.commands || []).map((cmd) => ({ ...cmd, _ts: c.timestamp })));

  const tabStyle = (t) => ({
    padding: "8px 20px",
    fontFamily: "JetBrains Mono",
    fontSize: 11,
    letterSpacing: 1.5,
    textTransform: "uppercase",
    cursor: "pointer",
    background: tab === t ? "rgba(0,255,136,0.08)" : "transparent",
    color: tab === t ? "#00ff88" : "#556655",
    border: "none",
    borderBottom: tab === t ? "2px solid #00ff88" : "2px solid transparent",
    transition: "all 0.15s",
  });

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#0a0f0a",
        color: "#ccddcc",
        fontFamily: "Rajdhani, sans-serif",
        padding: 0,
      }}
    >
      {/* Header */}
      <header
        style={{
          padding: "16px 24px",
          borderBottom: "1px solid rgba(0,255,136,0.12)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "rgba(0,255,136,0.02)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ fontFamily: "Rajdhani", fontSize: 22, fontWeight: 700, color: "#00ff88", letterSpacing: 2 }}>
            GOD MODE
          </div>
          <div style={{ fontSize: 11, color: "#445544", fontFamily: "JetBrains Mono", letterSpacing: 1 }}>
            COMMAND & CONTROL
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 16, fontSize: 12, fontFamily: "JetBrains Mono" }}>
          <StatusDot ok={connected} />
          <span style={{ color: connected ? "#00ff88" : "#ff4444" }}>
            {connected ? "CONNECTED" : "OFFLINE"}
          </span>
          {health && (
            <span style={{ color: "#445544" }}>
              {health.llm_provider?.toUpperCase()} / {health.model}
            </span>
          )}
          {battlePlan?.phase && (
            <span style={{ color: "#ffaa00", borderLeft: "1px solid rgba(0,255,136,0.12)", paddingLeft: 12 }}>
              PHASE: {battlePlan.phase.replace(/_/g, " ").toUpperCase()}
            </span>
          )}
        </div>
      </header>

      {/* Tabs */}
      <nav style={{ display: "flex", borderBottom: "1px solid rgba(0,255,136,0.08)", paddingLeft: 24, overflowX: "auto" }} aria-label="Dashboard navigation">
        <button style={tabStyle("overview")} onClick={() => setTab("overview")}>Overview</button>
        <button style={tabStyle("intel")} onClick={() => setTab("intel")}>Tactical Intel</button>
        <button style={tabStyle("battle")} onClick={() => setTab("battle")}>Battle Plan</button>
        <button style={tabStyle("analytics")} onClick={() => setTab("analytics")}>Analytics</button>
        <button style={tabStyle("units")} onClick={() => setTab("units")}>Units</button>
        <button style={tabStyle("commands")} onClick={() => setTab("commands")}>Command Log</button>
        <button style={tabStyle("manual")} onClick={() => setTab("manual")}>Manual Control</button>
        <button style={tabStyle("map")} onClick={() => setTab("map")}>Tactical Map</button>
        <button style={tabStyle("behavior")} onClick={() => setTab("behavior")}>AI Behavior</button>
        <button style={tabStyle("scenarios")} onClick={() => setTab("scenarios")}>Scenarios</button>
      </nav>

      <main style={{ padding: "16px 24px", maxWidth: 1200, paddingBottom: 48 }}>
        {/* OVERVIEW TAB */}
        {tab === "overview" && (
          <>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <StatCard label="Players" value={state.player_count ?? "\u2014"} sub={`${players.length} tracked`} />
              <StatCard label="Mission Time" value={fmtTime(state.mission_time_seconds)} />
              <StatCard label="OPFOR Squads" value={enemyUnits.length} accent="#ff8844" />
              <StatCard label="BLUFOR Squads" value={friendlyUnits.length} accent="#4488ff" />
              <StatCard label="Commands Sent" value={health?.total_commands_issued ?? "\u2014"} sub={`${health?.total_requests ?? 0} SITREPs`} />
              <StatCard label="Errors" value={health?.total_errors ?? "\u2014"} accent={health?.total_errors > 0 ? "#ff4444" : "#00ff88"} />
            </div>

            {/* Battle Phase & Difficulty at a Glance */}
            {(battlePlan?.phase || statsData?.difficulty) && (
              <>
                <SectionHeader>Battle Status</SectionHeader>
                <div style={{ display: "flex", gap: 24, fontSize: 13, fontFamily: "JetBrains Mono", flexWrap: "wrap" }}>
                  {battlePlan?.phase && <div>Phase: <span style={{ color: "#ffaa00" }}>{battlePlan.phase.replace(/_/g, " ").toUpperCase()}</span></div>}
                  {battlePlan?.turn != null && <div>Turn: <span style={{ color: "#00aaff" }}>{battlePlan.turn}</span></div>}
                  {statsData?.difficulty != null && <div>Difficulty: <span style={{ color: "#ffaa00" }}>x{statsData.difficulty.toFixed(2)}</span></div>}
                  {statsData?.player_kills != null && <div>K/D: <span style={{ color: "#00ff88" }}>{statsData.player_kills}/{statsData.ai_kills ?? 0}</span></div>}
                </div>
              </>
            )}

            <SectionHeader>Environment</SectionHeader>
            <div style={{ display: "flex", gap: 24, fontSize: 13, fontFamily: "JetBrains Mono", flexWrap: "wrap" }}>
              <div>Weather: <span style={{ color: "#00ff88" }}>{state.current_weather || "\u2014"}</span></div>
              <div>Time: <span style={{ color: "#ffcc66" }}>{state.time_of_day ? `${Math.floor(state.time_of_day)}:${String(Math.floor((state.time_of_day % 1) * 60)).padStart(2, "0")}` : "\u2014"}</span></div>
              <div>Wind: <span style={{ color: "#88bbff" }}>{fmt(state.wind_speed)} m/s @ {fmt(state.wind_direction)}</span></div>
              {tacticalIntel?.environment?.visibility_label && (
                <div>Visibility: <span style={{ color: tacticalIntel.environment.visibility_label === "poor" ? "#ff4444" : "#00ff88" }}>{tacticalIntel.environment.visibility_label}</span></div>
              )}
            </div>

            {state.current_objective && (
              <>
                <SectionHeader>Objective</SectionHeader>
                <div style={{ fontSize: 13, fontFamily: "JetBrains Mono", color: "#aacc88" }}>
                  {state.current_objective}
                </div>
              </>
            )}

            {events.length > 0 && (
              <>
                <SectionHeader>Recent Events</SectionHeader>
                <div style={{ maxHeight: 150, overflowY: "auto", fontSize: 12, fontFamily: "JetBrains Mono" }}>
                  {events.map((e, i) => (
                    <div key={i} style={{ color: "#667766", padding: "2px 0", borderBottom: "1px solid rgba(0,255,136,0.04)" }}>
                      <span style={{ color: "#445544", marginRight: 8 }}>&#9656;</span>{e}
                    </div>
                  ))}
                </div>
              </>
            )}

            <SectionHeader>Server Stats</SectionHeader>
            <div style={{ fontSize: 12, fontFamily: "JetBrains Mono", color: "#556655", lineHeight: 1.8 }}>
              <div>Uptime: {health ? fmtTime(health.uptime_seconds) : "\u2014"}</div>
              <div>Input tokens: {health?.total_input_tokens?.toLocaleString() ?? "\u2014"} | Output tokens: {health?.total_output_tokens?.toLocaleString() ?? "\u2014"}</div>
              <div>Error rate: {health ? fmt(health.total_errors / Math.max(health.total_requests, 1) * 100) : "\u2014"}%</div>
              <div>Sessions: {health?.active_sessions ?? 0} active | {health?.memory_messages ?? 0} messages in memory</div>
              {health?.circuit_breaker_open && <div style={{ color: "#ff4444" }}>Circuit breaker: OPEN (until {ts(health.circuit_breaker_open_until)})</div>}
              {health?.glm_endpoint && <div>GLM endpoint: {health.glm_endpoint}</div>}
            </div>

            {/* Quick response time sparkline on overview */}
            {statsData?.recent_response_times_ms?.length > 1 && (
              <>
                <SectionHeader>Response Times</SectionHeader>
                <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
                  <Sparkline data={statsData.recent_response_times_ms} color="#00aaff" width={180} height={28} label="Response time trend" />
                  <span style={{ fontSize: 11, fontFamily: "JetBrains Mono", color: "#667766" }}>
                    Last: <span style={{ color: "#00aaff" }}>{statsData.recent_response_times_ms.slice(-1)[0]}ms</span>
                  </span>
                </div>
              </>
            )}
          </>
        )}

        {/* TACTICAL INTEL TAB */}
        {tab === "intel" && (
          <TacticalIntelPanel tacticalIntel={tacticalIntel} />
        )}

        {/* BATTLE PLAN TAB */}
        {tab === "battle" && (
          <BattlePlanPanel battlePlan={battlePlan} />
        )}

        {/* ANALYTICS TAB */}
        {tab === "analytics" && (
          <AnalyticsPanel statsData={statsData} commandLog={commandLog} />
        )}

        {/* UNITS TAB */}
        {tab === "units" && (
          <>
            <SectionHeader>OPFOR Units ({enemyUnits.length})</SectionHeader>
            {enemyUnits.length === 0 ? (
              <div style={{ color: "#445544", fontSize: 12, fontFamily: "JetBrains Mono" }}>No OPFOR units tracked</div>
            ) : (
              enemyUnits.map((u, i) => <UnitRow key={i} unit={u} />)
            )}

            <SectionHeader>BLUFOR Units ({friendlyUnits.length})</SectionHeader>
            {friendlyUnits.length === 0 ? (
              <div style={{ color: "#445544", fontSize: 12, fontFamily: "JetBrains Mono" }}>No BLUFOR units tracked</div>
            ) : (
              friendlyUnits.map((u, i) => <UnitRow key={i} unit={u} />)
            )}

            <SectionHeader>Players ({players.length})</SectionHeader>
            {players.length === 0 ? (
              <div style={{ color: "#445544", fontSize: 12, fontFamily: "JetBrains Mono" }}>No players connected</div>
            ) : (
              players.map((p, i) => (
                <div key={i} style={{ display: "flex", gap: 16, fontSize: 12, fontFamily: "JetBrains Mono", padding: "4px 0", borderBottom: "1px solid rgba(0,255,136,0.06)" }}>
                  <span style={{ color: "#4488ff" }}>Player {p.player_id}</span>
                  <span style={{ color: "#667766" }}>HP: {((p.health_pct || 0) * 100).toFixed(0)}%</span>
                  <span style={{ color: "#556655" }}>{p.is_in_vehicle ? `In ${p.vehicle_type || "vehicle"}` : "On foot"}</span>
                </div>
              ))
            )}
          </>
        )}

        {/* COMMAND LOG TAB */}
        {tab === "commands" && (
          <>
            <SectionHeader>Command History ({allCommands.length} commands)</SectionHeader>
            <div ref={logRef} style={{ maxHeight: 500, overflowY: "auto" }}>
              {allCommands.length === 0 ? (
                <div style={{ color: "#445544", fontSize: 12, fontFamily: "JetBrains Mono" }}>No commands issued yet</div>
              ) : (
                [...allCommands].reverse().map((cmd, i) => <CommandEntry key={i} cmd={cmd} index={i} />)
              )}
            </div>
          </>
        )}

        {/* MANUAL CONTROL TAB */}
        {tab === "manual" && (
          <>
            <SectionHeader>Manual Command Injection</SectionHeader>
            <div style={{ fontSize: 12, color: "#556655", fontFamily: "JetBrains Mono", marginBottom: 14 }}>
              Send commands directly to the game, bypassing the LLM. Commands are merged into the next SITREP response.
            </div>
            <ManualCommandPanel onSend={fetchData} />

            <SectionHeader>Quick Actions</SectionHeader>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {[
                { label: "Storm", cmd: { command_type: "set_weather", parameters: "Storm" }, color: "#88bbff" },
                { label: "Fog", cmd: { command_type: "set_weather", parameters: "Foggy" }, color: "#aaaaaa" },
                { label: "Clear", cmd: { command_type: "set_weather", parameters: "Clear" }, color: "#ffcc66" },
                { label: "Night", cmd: { command_type: "set_time", parameters: "22.5" }, color: "#6666aa" },
                { label: "Dawn", cmd: { command_type: "set_time", parameters: "5.75" }, color: "#ff8844" },
                { label: "Cleanup Dead", cmd: { command_type: "cleanup_dead", parameters: "" }, color: "#667766" },
              ].map((q) => (
                <button
                  key={q.label}
                  onClick={async () => {
                    await fetch(`${serverUrl}/api/manual-command`, {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({
                        commands: [{ ...q.cmd, unit_id: "", target_position: "0,0,0", narrative: "", reasoning: "quick_action" }],
                        narrative_broadcast: "",
                        weather_command: "",
                        time_override: -1,
                      }),
                    });
                    fetchData();
                  }}
                  style={{
                    background: "transparent",
                    border: `1px solid ${q.color}44`,
                    color: q.color,
                    padding: "6px 16px",
                    fontFamily: "JetBrains Mono",
                    fontSize: 11,
                    cursor: "pointer",
                    letterSpacing: 0.5,
                  }}
                >
                  {q.label}
                </button>
              ))}
            </div>

            <SectionHeader>Mission Control</SectionHeader>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                onClick={async () => {
                  if (!window.confirm("Reset mission? This clears all state, memory, and battle plans.")) return;
                  await fetch(`${serverUrl}/api/reset`, { method: "POST" });
                  fetchData();
                }}
                style={{
                  background: "rgba(255,68,68,0.08)",
                  border: "1px solid rgba(255,68,68,0.25)",
                  color: "#ff4444",
                  padding: "8px 20px",
                  fontFamily: "JetBrains Mono",
                  fontSize: 11,
                  cursor: "pointer",
                  letterSpacing: 1,
                  textTransform: "uppercase",
                }}
              >
                Reset Mission
              </button>
            </div>
          </>
        )}

        {/* TACTICAL MAP TAB */}
        {tab === "map" && (
          <>
            <SectionHeader>Tactical Map</SectionHeader>
            <TacticalMap gameState={gameState} tacticalIntel={tacticalIntel} />
          </>
        )}

        {/* AI BEHAVIOR TAB */}
        {tab === "behavior" && (
          <>
            <SectionHeader>AI Commander Behavior</SectionHeader>
            <div style={{ fontSize: 12, color: "#556655", fontFamily: "JetBrains Mono", marginBottom: 14 }}>
              Adjust AI decision-making parameters. Changes take effect on the next SITREP cycle.
            </div>
            <AIBehaviorControls serverUrl={serverUrl} />
          </>
        )}

        {/* SCENARIOS TAB */}
        {tab === "scenarios" && (
          <>
            <SectionHeader>Scenario Library</SectionHeader>
            <div style={{ fontSize: 12, color: "#556655", fontFamily: "JetBrains Mono", marginBottom: 14 }}>
              Pre-built scenarios that deploy units, set weather, and configure the battlefield with one click.
            </div>
            <ScenarioLibrary serverUrl={serverUrl} fetchData={fetchData} />
          </>
        )}
      </main>

      {/* Footer */}
      <footer
        style={{
          position: "fixed",
          bottom: 0,
          left: 0,
          right: 0,
          padding: "6px 24px",
          background: "#0a0f0a",
          borderTop: "1px solid rgba(0,255,136,0.08)",
          display: "flex",
          justifyContent: "space-between",
          fontSize: 10,
          fontFamily: "JetBrains Mono",
          color: "#334433",
        }}
      >
        <span>SITREP age: {gameState?.age_seconds ? `${fmt(gameState.age_seconds)}s` : "\u2014"}</span>
        <span>
          {battlePlan?.phase ? `${battlePlan.phase.replace(/_/g, " ")} | ` : ""}
          {statsData?.difficulty != null ? `diff: x${statsData.difficulty.toFixed(2)} | ` : ""}
          Polling every {POLL_INTERVAL / 1000}s
        </span>
        <span>{new Date().toLocaleTimeString()}</span>
      </footer>
    </div>
  );
}
