import { useRef, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer
} from 'recharts'
import jsPDF from 'jspdf'
import html2canvas from 'html2canvas'

// ─── DATA SOURCES ─────────────────────────────────────────────────────────────
// GET /api/sites?user_id=              → cloudPool: cloud_sites + user_site_auth
// GET /api/metering/latest?site_id=    → cloudPool: cloud_metering_history (latest row)
// GET /api/metering/history?site_id=   → cloudPool: cloud_metering_history (24 rows)
// GET /api/metering/avg15min           → edgePool:  solar_edge.avg_15min
// GET /api/metering/forecast           → edgePool:  solar_edge.energy_forecasts
// GET /api/carbon/summary              → edgePool:  solar_edge.metering_history (SUM export)

// ─── SIDEBAR ──────────────────────────────────────────────────────────────────
function Sidebar({ navigate, isAdmin }) {
  return (
    <div className="w-14 bg-[#1a3a6b] flex flex-col items-center py-4 gap-3 shrink-0 shadow-lg">
      <div className="w-8 h-8 rounded-lg bg-white/10 flex items-center justify-center mb-2">
        <span className="text-base">⚡</span>
      </div>
      <NavBtn icon="🌐" onClick={() => navigate('/dashboard')} active />
      {isAdmin && <NavBtn icon="📋" onClick={() => navigate('/organization')} />}
      <div className="mt-auto">
        <NavBtn icon="🚪" onClick={() => navigate('/')} danger />
      </div>
    </div>
  )
}

function NavBtn({ icon, onClick, active, danger }) {
  return (
    <button onClick={onClick}
      className={`w-10 h-10 rounded-xl flex items-center justify-center text-lg transition-all cursor-pointer
        ${active ? 'bg-white/20 shadow-inner' : danger ? 'hover:bg-red-500/30' : 'hover:bg-white/10'}`}>
      {icon}
    </button>
  )
}

// ─── METRIC CARD ──────────────────────────────────────────────────────────────
function MetricCard({ icon, label, value, unit, color = 'text-[#1a3a6b]', small = false }) {
  return (
    <div className="bg-white rounded-xl border border-slate-100 shadow-sm px-4 py-3 flex flex-col gap-1 hover:shadow-md transition-shadow">
      <div className="flex items-center justify-between">
        <span className="text-slate-400 text-xs font-medium">{label}</span>
        <span className="text-base">{icon}</span>
      </div>
      <div className={`font-bold ${small ? 'text-lg' : 'text-2xl'} ${color} leading-tight`}>
        {value ?? '—'}
        <span className="text-xs font-normal text-slate-400 ml-1">{unit}</span>
      </div>
    </div>
  )
}

// ─── STATUS BADGE ─────────────────────────────────────────────────────────────
function StatusBadge({ ok, label }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-full border
      ${ok ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-red-50 text-red-600 border-red-200'}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${ok ? 'bg-emerald-500' : 'bg-red-500'}`} />
      {label}
    </span>
  )
}

// ─── PROGRESS BAR ─────────────────────────────────────────────────────────────
function ProgressBar({ label, value, max, colorClass, unit }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0
  const auto = pct > 80 ? 'bg-emerald-500' : pct > 40 ? 'bg-blue-500' : 'bg-amber-400'
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex justify-between text-xs">
        <span className="text-slate-500 font-medium">{label}</span>
        <span className="text-[#1a3a6b] font-bold">{value?.toFixed(0)}{unit}</span>
      </div>
      <div className="w-full bg-slate-100 rounded-full h-2">
        <div className={`h-2 rounded-full transition-all duration-500 ${colorClass || auto}`}
          style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// ─── GAUGE ────────────────────────────────────────────────────────────────────
function Gauge({ value, max }) {
  const pct = max > 0 ? Math.min(value / max, 1) : 0
  const R = 72, cx = 100, cy = 100
  const toRad = d => (d * Math.PI) / 180
  const startAngle = 210, sweep = 240
  const px = (deg, r) => cx + r * Math.cos(toRad(deg))
  const py = (deg, r) => cy + r * Math.sin(toRad(deg))
  const arc = (f, t, r) => {
    const [x1, y1, x2, y2] = [px(f, r), py(f, r), px(t, r), py(t, r)]
    return `M ${x1} ${y1} A ${r} ${r} 0 ${t - f > 180 ? 1 : 0} 1 ${x2} ${y2}`
  }
  const end = startAngle + sweep * pct
  const hue = pct < 0.5 ? '#3b82f6' : pct < 0.8 ? '#f59e0b' : '#ef4444'
  return (
    <svg viewBox="0 0 200 140" width="180" height="126">
      <defs>
        <linearGradient id="gaugeG2" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#3b82f6" />
          <stop offset="60%" stopColor="#f59e0b" />
          <stop offset="100%" stopColor="#ef4444" />
        </linearGradient>
      </defs>
      <path d={arc(startAngle, startAngle + sweep, R)} fill="none" stroke="#e2e8f0" strokeWidth="12" strokeLinecap="round" />
      <path d={arc(startAngle, end, R)} fill="none" stroke="url(#gaugeG2)" strokeWidth="12" strokeLinecap="round" />
      <circle cx={cx} cy={cy} r="8" fill="#f8fafc" stroke="#cbd5e1" strokeWidth="1.5" />
      <line x1={cx} y1={cy} x2={px(end, 56)} y2={py(end, 56)} stroke={hue} strokeWidth="2.5" strokeLinecap="round" />
      <circle cx={cx} cy={cy} r="4" fill={hue} />
      <text x={px(startAngle, R + 16)} y={py(startAngle, R + 16)} fill="#94a3b8" fontSize="8" textAnchor="middle">0</text>
      <text x={px(startAngle + sweep, R + 16)} y={py(startAngle + sweep, R + 16)} fill="#94a3b8" fontSize="8" textAnchor="middle">{max}</text>
    </svg>
  )
}

// ─── CHART TOOLTIP ────────────────────────────────────────────────────────────
const ChartTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-lg px-3 py-2.5 text-xs">
      <div className="font-semibold text-slate-600 mb-1.5">{label}</div>
      {payload.map((p, i) => p.value != null && (
        <div key={i} className="flex items-center gap-2 py-0.5">
          <span className="w-2 h-2 rounded-full" style={{ background: p.color }} />
          <span className="text-slate-500">{p.name}:</span>
          <span className="font-bold text-slate-800">{p.value} kW</span>
        </div>
      ))}
    </div>
  )
}

// ─── SECTION HEADER ───────────────────────────────────────────────────────────
function SectionHeader({ title, subtitle, icon }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <span className="text-base">{icon}</span>
      <div>
        <h2 className="text-sm font-bold text-[#1a3a6b]">{title}</h2>
        {subtitle && <p className="text-xs text-slate-400">{subtitle}</p>}
      </div>
    </div>
  )
}

// ─── DASHBOARD ────────────────────────────────────────────────────────────────
function Dashboard({ user }) {
  const navigate  = useNavigate()
  const isAdmin   = user?.role === 'ADMIN'
  const reportRef = useRef(null)
  const fmt = (v, d = 1) => v != null ? parseFloat(v).toFixed(d) : '—'

  // ── state ──
  const [sites,          setSites]          = useState([])
  const [selectedSiteId, setSelectedSiteId] = useState(null)
  const [metering,       setMetering]       = useState(null)
  const [forecastChart,  setForecastChart]  = useState([])
  const [nowIndex,       setNowIndex]       = useState(-1)
  const [carbon,         setCarbon]         = useState(null)
  const [loading,        setLoading]        = useState(true)

  const selectedSite = sites.find(s => s.site_id === selectedSiteId) || null

  // ── fetch helpers (เรียกซ้ำได้จาก interval) ──────────────────────────────
  const fetchMetering = (siteId) => {
    if (!siteId) return
    fetch(`/api/metering/latest?site_id=${siteId}`)
      .then(r => r.json())
      .then(d => { if (d.success) setMetering(d.metering) })
      .catch(console.error)
  }

  const fetchForecastChart = () => {
    const snap15 = t => {
      const d = new Date(t)
      d.setMinutes(Math.floor(d.getMinutes() / 15) * 15, 0, 0)
      return d.getTime()
    }
    Promise.all([
      fetch('/api/metering/avg15min').then(r => r.json()),
      fetch('/api/metering/forecast').then(r => r.json()),
    ]).then(([avgData, fcData]) => {
      const forecastMap = {}
      if (fcData.success) {
        fcData.data.forEach(r => {
          forecastMap[snap15(r.target_time)] = {
            pv_forecast:   parseFloat(r.solar_gen_forecast) || 0,
            load_forecast: parseFloat(r.load_cons_forecast) || 0,
          }
        })
      }
      const rows = []
      if (avgData.success) {
        avgData.data.forEach(r => {
          const ts = snap15(r.timestamp)
          const fc = forecastMap[ts]
          rows.push({
            time:          new Date(r.timestamp).toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' }),
            pv_actual:     parseFloat(r.pv_power_kw)  || 0,
            load_actual:   parseFloat(r.load_power_kw) || 0,
            pv_forecast:   fc?.pv_forecast   ?? 0,
            load_forecast: fc?.load_forecast ?? 0,
          })
        })
      }
      setForecastChart(rows)
    }).catch(console.error)
  }

  const fetchCarbon = () => {
    fetch('/api/carbon/summary')
      .then(r => r.json())
      .then(d => { if (d.success) setCarbon(d) })
      .catch(console.error)
  }

  // ── 1. load sites on mount ──
  useEffect(() => {
    if (!user?.user_id) return
    fetch(`/api/sites?user_id=${user.user_id}`)
      .then(r => r.json())
      .then(d => {
        if (d.success && d.sites.length > 0) {
          setSites(d.sites)
          setSelectedSiteId(d.sites[0].site_id)
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [user])

  // ── 2. metering: fetch เมื่อเลือก site + refresh ทุก 1 นาที ──
  useEffect(() => {
    if (!selectedSiteId) return
    setMetering(null)
    fetchMetering(selectedSiteId)

    const interval = setInterval(() => fetchMetering(selectedSiteId), 60_000)
    return () => clearInterval(interval)
  }, [selectedSiteId])

  // ── 3. forecast chart: fetch ตอนโหลด + refresh ทุก 1 นาที ──
  useEffect(() => {
    fetchForecastChart()
    const interval = setInterval(fetchForecastChart, 60_000)
    return () => clearInterval(interval)
  }, [])

  // ── 4. carbon: fetch ตอนโหลด + refresh ทุก 1 นาที ──
  useEffect(() => {
    fetchCarbon()
    const interval = setInterval(fetchCarbon, 60_000)
    return () => clearInterval(interval)
  }, [])

  // ── PDF download ──
  const handleDownload = async () => {
    const el = reportRef.current
    if (!el) return
    const canvas  = await html2canvas(el, { backgroundColor: '#f8fafc', scale: 2, useCORS: true })
    const imgData = canvas.toDataURL('image/png')
    const pdf     = new jsPDF({ orientation: 'landscape', unit: 'mm', format: 'a4' })
    const pw = pdf.internal.pageSize.getWidth(), ph = pdf.internal.pageSize.getHeight()
    const ratio = Math.min(pw / canvas.width, ph / canvas.height)
    pdf.addImage(imgData, 'PNG', (pw - canvas.width * ratio) / 2, (ph - canvas.height * ratio) / 2, canvas.width * ratio, canvas.height * ratio)
    const d = new Date()
    pdf.save(`gridmind-${d.getFullYear()}${String(d.getMonth()+1).padStart(2,'0')}${String(d.getDate()).padStart(2,'0')}.pdf`)
  }

  if (loading) return (
    <div className="h-screen bg-slate-50 flex items-center justify-center">
      <span className="text-slate-400 text-sm">กำลังโหลด...</span>
    </div>
  )

  const m    = metering || {}
  const s    = selectedSite || {}
  const maxPv = parseFloat(s.pv_capacity_kwp) || 100



  // carbon values
  const EMISSION_FACTOR = carbon?.emission_factor ?? 0.4999
  const carbonToday = carbon?.today?.carbon_tco2e     ?? 0
  const carbonMonth = carbon?.this_month?.carbon_tco2e ?? 0
  const carbonYear  = carbon?.this_year?.carbon_tco2e  ?? 0
  const treesEquiv  = (carbonToday * 1000 / 21.77).toFixed(0)
  const carbonTarget = 0.05 // tCO₂e/day — ปรับตาม target จริงของ org ได้
  const carbonPct   = Math.min((carbonToday / carbonTarget) * 100, 100)

  return (
    <div className="h-screen bg-slate-50 flex overflow-hidden" style={{ fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif" }}>
      <Sidebar navigate={navigate} isAdmin={isAdmin} />

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

        {/* ── TOP BAR ── */}
        <div className="bg-white border-b border-slate-200 px-5 py-3 flex items-center justify-between gap-4 shrink-0">
          <div className="flex items-center gap-3 flex-wrap">
            <div>
              <div className="text-xs text-slate-400 font-medium uppercase tracking-widest">GridMind Platform</div>
              <h1 className="text-base font-bold text-[#1a3a6b] leading-tight">{s.site_name || 'Dashboard'}</h1>
            </div>
            {/* Site selector */}
            {sites.length > 0 && (
              <select
                value={selectedSiteId || ''}
                onChange={e => setSelectedSiteId(e.target.value)}
                className="text-xs font-medium text-[#1a3a6b] bg-slate-50 border border-slate-200 rounded-lg px-3 py-1.5 cursor-pointer outline-none hover:border-blue-400 transition-colors">
                {sites.map(site => (
                  <option key={site.site_id} value={site.site_id}>{site.site_name}</option>
                ))}
              </select>
            )}
            <div className="flex items-center gap-2 flex-wrap">
              <StatusBadge ok={!!s.is_online} label={s.is_online ? 'Online' : 'Offline'} />
              {s.system_type && (
                <span className="text-xs text-slate-400 bg-slate-50 border border-slate-200 px-2.5 py-1 rounded-full">
                  {s.system_type} · {s.province}
                </span>
              )}

              {m.last_sync_from_edge && (
                <span className="text-xs text-slate-400">
                  อัปเดต: {new Date(m.last_sync_from_edge).toLocaleString('th-TH')}
                </span>
              )}
            </div>
          </div>
          <button onClick={handleDownload}
            className="flex items-center gap-2 bg-[#1a3a6b] hover:bg-[#15306b] text-white text-xs font-semibold px-4 py-2 rounded-lg transition-colors cursor-pointer shrink-0 shadow-sm">
            ⬇ ดาวน์โหลด PDF
          </button>
        </div>

        {/* ── CONTENT ── */}
        <div className="flex-1 overflow-y-auto p-4">
          <div ref={reportRef} className="flex flex-col gap-4">

            {/* ── ROW 1: Gauge + Battery + KPI Cards ── */}
            <div className="grid grid-cols-12 gap-4">

              {/* PV Gauge */}
              <div className="col-span-12 sm:col-span-3 bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex flex-col items-center">
                <SectionHeader icon="☀️" title="Solar Output" subtitle="กำลังผลิตปัจจุบัน" />
                <Gauge value={parseFloat(m.pv_power_kw) || 0} max={maxPv} />
                <div className="text-2xl font-bold text-[#1a3a6b] -mt-2">{fmt(m.pv_power_kw)}</div>
                <div className="text-xs text-slate-400 mb-2">kW จาก {maxPv} kWp</div>
                <div className="w-full bg-slate-100 rounded-full h-1.5">
                  <div className="h-1.5 rounded-full bg-blue-500 transition-all"
                    style={{ width: `${Math.min(((parseFloat(m.pv_power_kw) || 0) / maxPv) * 100, 100)}%` }} />
                </div>
                <div className="text-xs text-slate-400 mt-1 self-end">
                  {(((parseFloat(m.pv_power_kw) || 0) / maxPv) * 100).toFixed(1)}% ของ capacity
                </div>
              </div>

              {/* Battery */}
              <div className="col-span-12 sm:col-span-3 bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex flex-col gap-3">
                <SectionHeader icon="🔋" title="Battery" subtitle="สถานะแบตเตอรี่" />
                <ProgressBar label="State of Charge" value={parseFloat(m.batt_soc) || 0} max={100} colorClass="bg-blue-500" unit="%" />
                <ProgressBar label="Min SOC Setting"  value={parseFloat(m.min_batt_soc) || 0} max={100} colorClass="bg-slate-300" unit="%" />
                <div className="mt-1 bg-slate-50 rounded-lg px-3 py-2 flex items-center justify-between">
                  <span className="text-xs text-slate-500">Battery Power</span>
                  <span className={`text-sm font-bold ${parseFloat(m.batt_power_kw) < 0 ? 'text-blue-600' : 'text-amber-600'}`}>
                    {parseFloat(m.batt_power_kw) < 0 ? '▼ Charging' : '▲ Discharging'} {Math.abs(parseFloat(m.batt_power_kw) || 0).toFixed(1)} kW
                  </span>
                </div>
                <div className="text-xs text-slate-400 text-center">{s.battery_capacity_kwh ?? '—'} kWh installed</div>
              </div>

              {/* KPI Cards */}
              <div className="col-span-12 sm:col-span-6 grid grid-cols-3 gap-3">
                <MetricCard icon="☀️" label="PV Power"      value={fmt(m.pv_power_kw)}      unit="kW"  color="text-amber-600" />
                <MetricCard icon="🏭" label="Load Power"    value={fmt(m.load_power_kw)}     unit="kW"  color="text-[#1a3a6b]" />
                <MetricCard icon="⬇" label="Grid Import"   value={fmt(m.grid_import_kw)}    unit="kW"  color="text-red-600" />
                <MetricCard icon="⬆" label="Grid Export"   value={fmt(m.grid_export_kw)}    unit="kW"  color="text-emerald-600" />
                <MetricCard icon="📥" label="Import Today"  value={fmt(m.energy_import_kwh)} unit="kWh" color="text-slate-600" small />
                <MetricCard icon="📤" label="Export Today"  value={fmt(m.energy_export_kwh)} unit="kWh" color="text-emerald-600" small />
              </div>
            </div>

            {/* ── ROW 2: Forecast vs Actual (avg_15min × energy_forecasts) ── */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
              <div className="flex items-center justify-between mb-1">
                <SectionHeader icon="📈" title="Forecast vs Actual (15-min)"
                  subtitle="เปรียบเทียบย้อนหลัง 4h · เส้นทึบ = ค่าจริง (avg_15min) · เส้นประ = ที่เคย forecast ไว้ (energy_forecasts)" />
                <div className="hidden sm:flex items-center gap-3 text-xs text-slate-400 shrink-0">
                  <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-amber-500 inline-block" /> PV จริง</span>
                  <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-amber-300 inline-block border-dashed" /> PV forecast</span>
                  <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-blue-600 inline-block" /> Load จริง</span>
                  <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-blue-300 inline-block border-dashed" /> Load forecast</span>
                </div>
              </div>
              {forecastChart.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={forecastChart} margin={{ left: -10, right: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#94a3b8' }} interval={2} tickLine={false} axisLine={{ stroke: '#e2e8f0' }} />
                    <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickLine={false} axisLine={false} unit=" kW" />
                    <Tooltip content={<ChartTooltip />} />
                    <Line type="monotone" dataKey="pv_actual"     name="PV จริง"      stroke="#f59e0b" strokeWidth={2}   dot={false} connectNulls={false} />
                    <Line type="monotone" dataKey="pv_forecast"   name="PV forecast"  stroke="#fcd34d" strokeWidth={1.5} dot={false} strokeDasharray="5 3" />
                    <Line type="monotone" dataKey="load_actual"   name="Load จริง"    stroke="#1d4ed8" strokeWidth={2}   dot={false} connectNulls={false} />
                    <Line type="monotone" dataKey="load_forecast" name="Load forecast" stroke="#93c5fd" strokeWidth={1.5} dot={false} strokeDasharray="5 3" />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex items-center justify-center h-32 text-slate-400 text-sm">ไม่มีข้อมูล forecast</div>
              )}
            </div>


            {/* ── ROW 3: Carbon Reduction (solar_edge.metering_history) ── */}
            {/* สูตร: Carbon (tCO₂e) = SUM(grid_export_kw)/60 × 0.4999 / 1000 */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <SectionHeader icon="🌿" title="Carbon Reduction" subtitle="การลดการปล่อยก๊าซเรือนกระจก" />
                  <div className="text-xs text-slate-400 -mt-2 ml-6">
                    สูตร: Energy Export (kWh) × {EMISSION_FACTOR} tCO₂e/MWh ÷ 1,000 &nbsp;·&nbsp; Emission Factor: On-Grid ไทย (สำนักงาน คพ.)
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="bg-emerald-50 rounded-xl border border-emerald-200 px-4 py-3 flex flex-col gap-1">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-emerald-600">วันนี้</span>
                    <span className="text-base">🌿</span>
                  </div>
                  <div className="text-2xl font-bold text-emerald-700 leading-tight">
                    {(carbonToday * 1000).toFixed(2)}
                    <span className="text-xs font-normal text-emerald-500 ml-1">kgCO₂e</span>
                  </div>
                  <div className="text-xs text-emerald-500">= {carbonToday.toFixed(4)} tCO₂e</div>
                </div>
                <div className="bg-slate-50 rounded-xl border border-slate-200 px-4 py-3 flex flex-col gap-1">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-500">เดือนนี้</span>
                    <span className="text-base">📅</span>
                  </div>
                  <div className="text-xl font-bold text-[#1a3a6b] leading-tight">
                    {(carbonMonth * 1000).toFixed(1)}
                    <span className="text-xs font-normal text-slate-400 ml-1">kgCO₂e</span>
                  </div>
                  <div className="text-xs text-slate-400">{carbonMonth.toFixed(3)} tCO₂e</div>
                </div>
                <div className="bg-slate-50 rounded-xl border border-slate-200 px-4 py-3 flex flex-col gap-1">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-500">ปีนี้</span>
                    <span className="text-base">📆</span>
                  </div>
                  <div className="text-xl font-bold text-[#1a3a6b] leading-tight">
                    {carbonYear.toFixed(2)}
                    <span className="text-xs font-normal text-slate-400 ml-1">tCO₂e</span>
                  </div>
                  <div className="text-xs text-slate-400">{(carbonYear * 1000).toFixed(0)} kgCO₂e</div>
                </div>
                <div className="bg-slate-50 rounded-xl border border-slate-200 px-4 py-3 flex flex-col gap-1">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-500">เทียบเท่าต้นไม้วันนี้</span>
                    <span className="text-base">🌳</span>
                  </div>
                  <div className="text-xl font-bold text-[#1a3a6b] leading-tight">
                    {treesEquiv}
                    <span className="text-xs font-normal text-slate-400 ml-1">ต้น/วัน</span>
                  </div>
                  <div className="text-xs text-slate-400">≈ 21.77 kgCO₂/ต้น/ปี</div>
                </div>
              </div>
              <div className="mt-4 pt-4 border-t border-slate-100">
                <div className="flex justify-between text-xs text-slate-400 mb-1.5">
                  <span className="font-medium text-slate-500">เป้าหมายลดคาร์บอนวันนี้</span>
                  <span className="font-bold text-emerald-600">
                    {carbonPct.toFixed(1)}% จากเป้า {(carbonTarget * 1000).toFixed(0)} kgCO₂e
                  </span>
                </div>
                <div className="w-full bg-slate-100 rounded-full h-2">
                  <div className="h-2 rounded-full bg-emerald-500 transition-all duration-500"
                    style={{ width: `${carbonPct}%` }} />
                </div>
              </div>
            </div>

            {/* ── ROW 5: Environmental & Grid (cloud_metering_history) ── */}
            <div>
              <SectionHeader icon="🌤" title="Environmental & Grid" subtitle="ค่าสภาพแวดล้อมและระบบไฟฟ้า" />
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                <MetricCard icon="☀️"  label="Irradiance"   value={m.irradiance_wm2}             unit="W/m²" color="text-amber-600"   small />
                <MetricCard icon="🌡️" label="Ambient Temp" value={fmt(m.ambient_temp_c)}         unit="°C"   color="text-orange-600"  small />
                <MetricCard icon="🔆"  label="Panel Temp"   value={fmt(m.panel_temp_c)}           unit="°C"   color="text-red-500"     small />
                <MetricCard icon="🔌"  label="Grid Voltage" value={m.grid_voltage_v}              unit="V"    color="text-[#1a3a6b]"  small />
                <MetricCard icon="〰" label="Frequency"     value={fmt(m.grid_frequency_hz, 2)}   unit="Hz"   color="text-slate-600"   small />
              </div>
            </div>

          </div>
        </div>
      </div>
    </div>
  )
}

export default Dashboard