import express from 'express'
import mysql2 from 'mysql2/promise'
import bcrypt from 'bcryptjs'
import cors from 'cors'

const app = express()
app.use(cors({ origin: 'http://localhost:5173' }))
app.use(express.json())

// ─── CONNECTION POOLS ─────────────────────────────────────────────────────────
// gridmind_cloud  → org/site/user และ full_history
// solar_edge      → metering_history, avg_15min, energy_forecasts (edge device)
const cloudPool = mysql2.createPool({
  host: '127.0.0.1', user: 'root', password: '', database: 'solar_cloud', waitForConnections: true,
})
const edgePool = mysql2.createPool({
  host: '127.0.0.1', user: 'root', password: '', database: 'solar_edge', waitForConnections: true,
})

// ─── AUTH ─────────────────────────────────────────────────────────────────────
app.post('/api/auth/login', async (req, res) => {
  const { email, password } = req.body
  if (!email || !password) return res.status(400).json({ success: false, message: 'Email and password are required' })
  try {
    const [rows] = await cloudPool.execute(
      'SELECT user_id, email, full_name, password_hash, role, is_active FROM users WHERE email = ? LIMIT 1', [email])
    if (rows.length === 0) return res.status(401).json({ success: false, message: 'Username or Password incorrect' })
    const user = rows[0]
    if (!user.is_active) return res.status(403).json({ success: false, message: 'Account is inactive' })
    const isMatch = await bcrypt.compare(password, user.password_hash)
    if (!isMatch) return res.status(401).json({ success: false, message: 'Username or Password incorrect' })
    await cloudPool.execute('UPDATE users SET last_login = NOW() WHERE user_id = ?', [user.user_id])
    return res.json({ success: true, user: { user_id: user.user_id, email: user.email, full_name: user.full_name, role: user.role } })
  } catch (err) { console.error(err); return res.status(500).json({ success: false, message: 'Database error' }) }
})

// ─── SITES ────────────────────────────────────────────────────────────────────
app.get('/api/sites', async (req, res) => {
  const { user_id } = req.query
  if (!user_id) return res.status(400).json({ success: false, message: 'user_id is required' })
  try {
    const [rows] = await cloudPool.execute(
      `SELECT cs.site_id, cs.org_id, cs.site_name, cs.system_type, cs.pv_capacity_kwp,
              cs.battery_capacity_kwh, cs.grid_capacity_kw, cs.province, cs.is_online, cs.last_heartbeat
       FROM sites cs
       INNER JOIN user_site_auth usa ON cs.site_id = usa.site_id
       WHERE usa.user_id = ? AND usa.is_active = 1 AND cs.is_active = 1`, [user_id])
    return res.json({ success: true, sites: rows })
  } catch (err) { console.error(err); return res.status(500).json({ success: false, message: 'Database error' }) }
})

// ─── METERING: latest snapshot (cloudPool.full_history) ─────────────
app.get('/api/metering/latest', async (req, res) => {
  const { site_id } = req.query
  if (!site_id) return res.status(400).json({ success: false, message: 'site_id is required' })
  try {
    const [rows] = await cloudPool.execute(
      `SELECT pv_power_kw, load_power_kw, batt_power_kw, grid_import_kw, grid_export_kw, batt_soc,
              max_pv_power_kw, max_load_power_kw, min_batt_soc, energy_import_kwh, energy_export_kwh,
              irradiance_wm2, ambient_temp_c, panel_temp_c, grid_voltage_v, grid_frequency_hz,
              sample_count, last_sync_from_edge, timestamp
       FROM full_history WHERE site_id = ? ORDER BY timestamp DESC LIMIT 1`, [site_id])
    return res.json({ success: true, metering: rows[0] || null })
  } catch (err) { console.error(err); return res.status(500).json({ success: false, message: 'Database error' }) }
})

// ─── METERING: 24h trend (cloudPool.full_history) ──────────────────
app.get('/api/metering/history', async (req, res) => {
  const { site_id } = req.query
  if (!site_id) return res.status(400).json({ success: false, message: 'site_id is required' })
  try {
    const [rows] = await cloudPool.execute(
      `SELECT pv_power_kw, load_power_kw, batt_soc, timestamp
       FROM full_history
       WHERE site_id = ? ORDER BY timestamp DESC LIMIT 24`, [site_id])
    return res.json({ success: true, history: rows.reverse() })
  } catch (err) { console.error(err); return res.status(500).json({ success: false, message: 'Database error' }) }
})

// ─── AVG 15-MIN actual (edgePool.avg_15min) ───────────────────────────────────
// 16 จุดล่าสุด (4 ชั่วโมง) สำหรับฝั่ง "actual" ของกราฟ Forecast vs Actual
app.get('/api/metering/avg15min', async (req, res) => {
  try {
    const [rows] = await edgePool.execute(
      `SELECT timestamp, pv_power_kw, load_power_kw, irradiance_wm2
       FROM avg_15min
       ORDER BY timestamp DESC LIMIT 16`)
    return res.json({ success: true, data: rows.reverse() })
  } catch (err) { console.error(err); return res.status(500).json({ success: false, message: 'Database error' }) }
})

// ─── FORECAST (edgePool.energy_forecasts) ────────────────────────────────────
// ดึง forecast ที่ target_time ตรงกับช่วง avg_15min 4h ที่ผ่านมา
// เปรียบเทียบย้อนหลัง: ค่า forecast ที่ถูก generate ไว้ vs ค่าจริงจาก avg_15min
app.get('/api/metering/forecast', async (req, res) => {
  try {
    const [rows] = await edgePool.execute(
      `SELECT target_time, solar_gen_forecast, load_cons_forecast, net_energy_kw, clear_sky_kt
       FROM energy_forecasts
       WHERE target_time >= DATE_SUB(NOW(), INTERVAL 4 HOUR)
         AND target_time <= NOW()
       ORDER BY target_time ASC`)
    return res.json({ success: true, data: rows })
  } catch (err) { console.error(err); return res.status(500).json({ success: false, message: 'Database error' }) }
})

// ─── CARBON SUMMARY (edgePool.metering_history) ───────────────────────────────
// Carbon Reduction (tCO₂e) = SUM(grid_export_kw) / 60 × 0.4999 / 1000
// หาร 60 เพราะ metering_history เก็บค่า kW ทุก 1 นาที → แปลงเป็น kWh
// Emission Factor = 0.4999 tCO₂e/MWh (On-Grid ประเทศไทย, สำนักงาน คพ.)
app.get('/api/carbon/summary', async (req, res) => {
  const EMISSION_FACTOR = 0.4999
  try {
    const [[todayRow]] = await edgePool.execute(
      `SELECT COALESCE(SUM(grid_export_kw) / 60.0, 0) AS kwh
       FROM metering_history WHERE DATE(timestamp) = CURDATE()`)

    const [[monthRow]] = await edgePool.execute(
      `SELECT COALESCE(SUM(grid_export_kw) / 60.0, 0) AS kwh
       FROM metering_history
       WHERE YEAR(timestamp) = YEAR(CURDATE()) AND MONTH(timestamp) = MONTH(CURDATE())`)

    const [[yearRow]] = await edgePool.execute(
      `SELECT COALESCE(SUM(grid_export_kw) / 60.0, 0) AS kwh
       FROM metering_history WHERE YEAR(timestamp) = YEAR(CURDATE())`)

    const toCarbon = kwh => parseFloat(kwh) * EMISSION_FACTOR / 1000

    return res.json({
      success: true,
      emission_factor: EMISSION_FACTOR,
      today:      { export_kwh: +parseFloat(todayRow.kwh).toFixed(3),  carbon_tco2e: +toCarbon(todayRow.kwh).toFixed(6) },
      this_month: { export_kwh: +parseFloat(monthRow.kwh).toFixed(3),  carbon_tco2e: +toCarbon(monthRow.kwh).toFixed(4) },
      this_year:  { export_kwh: +parseFloat(yearRow.kwh).toFixed(3),   carbon_tco2e: +toCarbon(yearRow.kwh).toFixed(4) },
    })
  } catch (err) { console.error(err); return res.status(500).json({ success: false, message: 'Database error' }) }
})

// ─── ORGANIZATIONS CRUD ───────────────────────────────────────────────────────
app.get('/api/organizations', async (req, res) => {
  try {
    const [orgs] = await cloudPool.execute(
      `SELECT o.*,
        (SELECT COUNT(*) FROM sites cs WHERE cs.org_id = o.org_id AND cs.is_active = 1) AS site_count,
        (SELECT COUNT(DISTINCT usa.user_id) FROM user_site_auth usa WHERE usa.org_id = o.org_id AND usa.is_active = 1) AS user_count
       FROM organizations o ORDER BY o.created_at DESC`)
    return res.json({ success: true, organizations: orgs })
  } catch (err) { console.error(err); return res.status(500).json({ success: false, message: 'Database error' }) }
})

app.post('/api/organizations', async (req, res) => {
  const { org_name, org_type, subscription_plan, max_sites, billing_email, billing_address, tax_id } = req.body
  const org_id = 'org-' + Date.now()
  try {
    await cloudPool.execute(
      `INSERT INTO organizations (org_id, org_name, org_type, subscription_plan, max_sites, billing_email, billing_address, tax_id, is_active)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)`,
      [org_id, org_name, org_type, subscription_plan, max_sites || 5, billing_email || null, billing_address || null, tax_id || null])
    return res.json({ success: true, org_id })
  } catch (err) { console.error(err); return res.status(500).json({ success: false, message: 'Database error' }) }
})

app.put('/api/organizations/:org_id', async (req, res) => {
  const { org_name, org_type, subscription_plan, max_sites, billing_email, billing_address, tax_id, is_active } = req.body
  try {
    await cloudPool.execute(
      `UPDATE organizations SET org_name=?, org_type=?, subscription_plan=?, max_sites=?, billing_email=?, billing_address=?, tax_id=?, is_active=?, updated_at=NOW() WHERE org_id=?`,
      [org_name, org_type, subscription_plan, max_sites, billing_email, billing_address, tax_id, is_active, req.params.org_id])
    return res.json({ success: true })
  } catch (err) { console.error(err); return res.status(500).json({ success: false, message: 'Database error' }) }
})

app.delete('/api/organizations/:org_id', async (req, res) => {
  try {
    await cloudPool.execute(`DELETE FROM organizations WHERE org_id = ?`, [req.params.org_id])
    return res.json({ success: true })
  } catch (err) { console.error(err); return res.status(500).json({ success: false, message: 'Database error' }) }
})

// ─── SITES CRUD ───────────────────────────────────────────────────────────────
app.get('/api/organizations/:org_id/sites', async (req, res) => {
  try {
    const [sites] = await cloudPool.execute(
      `SELECT * FROM sites WHERE org_id = ? AND is_active = 1 ORDER BY created_at DESC`, [req.params.org_id])
    return res.json({ success: true, sites })
  } catch (err) { return res.status(500).json({ success: false, message: 'Database error' }) }
})

app.post('/api/organizations/:org_id/sites', async (req, res) => {
  const { site_name, system_type, pv_capacity_kwp, battery_capacity_kwh, grid_capacity_kw, province } = req.body
  const site_id = 'site-' + Date.now()
  try {
    await cloudPool.execute(
      `INSERT INTO sites (site_id, org_id, site_name, system_type, pv_capacity_kwp, battery_capacity_kwh, grid_capacity_kw, province, timezone, is_online, is_active)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Asia/Bangkok', 0, 1)`,
      [site_id, req.params.org_id, site_name, system_type, pv_capacity_kwp || null, battery_capacity_kwh || null, grid_capacity_kw || null, province || null])
    return res.json({ success: true, site_id })
  } catch (err) { console.error(err); return res.status(500).json({ success: false, message: 'Database error' }) }
})

app.put('/api/organizations/:org_id/sites/:site_id', async (req, res) => {
  const { site_name, system_type, pv_capacity_kwp, battery_capacity_kwh, grid_capacity_kw, province } = req.body
  try {
    await cloudPool.execute(
      `UPDATE sites SET site_name=?, system_type=?, pv_capacity_kwp=?, battery_capacity_kwh=?, grid_capacity_kw=?, province=?, updated_at=NOW() WHERE site_id=? AND org_id=?`,
      [site_name, system_type, pv_capacity_kwp || null, battery_capacity_kwh || null, grid_capacity_kw || null, province || null, req.params.site_id, req.params.org_id])
    return res.json({ success: true })
  } catch (err) { return res.status(500).json({ success: false, message: 'Database error' }) }
})

app.delete('/api/organizations/:org_id/sites/:site_id', async (req, res) => {
  try {
    await cloudPool.execute(
      `DELETE FROM sites WHERE site_id = ? AND org_id = ?`,
      [req.params.site_id, req.params.org_id])
    return res.json({ success: true })
  } catch (err) { return res.status(500).json({ success: false, message: 'Database error' }) }
})

// ─── USERS CRUD ───────────────────────────────────────────────────────────────
app.get('/api/organizations/:org_id/users', async (req, res) => {
  try {
    const [users] = await cloudPool.execute(
      `SELECT DISTINCT u.user_id, u.full_name, u.email, u.role, u.is_active, u.last_login
       FROM users u
       INNER JOIN user_site_auth usa ON u.user_id = usa.user_id
       WHERE usa.org_id = ? AND usa.is_active = 1 ORDER BY u.created_at DESC`, [req.params.org_id])
    return res.json({ success: true, users })
  } catch (err) { return res.status(500).json({ success: false, message: 'Database error' }) }
})

app.post('/api/organizations/:org_id/users', async (req, res) => {
  const { full_name, email, password, role } = req.body
  const user_id = 'user-' + Date.now()
  try {
    const hash = await bcrypt.hash(password, 10)
    await cloudPool.execute(
      `INSERT INTO users (user_id, email, password_hash, full_name, role, is_active, email_verified) VALUES (?, ?, ?, ?, ?, 1, 1)`,
      [user_id, email, hash, full_name, role || 'VIEWER'])
    const [sites] = await cloudPool.execute(
      `SELECT site_id FROM sites WHERE org_id = ? AND is_active = 1`, [req.params.org_id])
    for (const s of sites) {
      await cloudPool.execute(
        `INSERT INTO user_site_auth (user_id, org_id, site_id, permission_level) VALUES (?, ?, ?, ?)`,
        [user_id, req.params.org_id, s.site_id, role === 'ADMIN' ? 'ADMIN' : 'VIEWER'])
    }
    return res.json({ success: true, user_id })
  } catch (err) {
    console.error(err)
    return res.status(500).json({ success: false, message: err.code === 'ER_DUP_ENTRY' ? 'Email already exists' : 'Database error' })
  }
})

app.put('/api/organizations/:org_id/users/:user_id', async (req, res) => {
  const { full_name, email, role, is_active } = req.body
  try {
    await cloudPool.execute(
      `UPDATE users SET full_name=?, email=?, role=?, is_active=?, updated_at=NOW() WHERE user_id=?`,
      [full_name, email, role, is_active, req.params.user_id])
    return res.json({ success: true })
  } catch (err) { return res.status(500).json({ success: false, message: 'Database error' }) }
})

app.delete('/api/organizations/:org_id/users/:user_id', async (req, res) => {
  try {
    await cloudPool.execute(`DELETE FROM user_site_auth WHERE user_id = ? AND org_id = ?`, [req.params.user_id, req.params.org_id])
    await cloudPool.execute(`DELETE FROM users WHERE user_id = ?`, [req.params.user_id])
    return res.json({ success: true })
  } catch (err) { return res.status(500).json({ success: false, message: 'Database error' }) }
})

const PORT = 3000
app.listen(PORT, () => console.log(`Backend running at http://localhost:${PORT}`))