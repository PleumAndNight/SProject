import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

// ─── SIDEBAR  ───────────────────────────────────────────────
function Sidebar({ navigate }) {
  return (
    <div className="w-14 bg-[#1a3a6b] flex flex-col items-center py-4 gap-3 shrink-0 shadow-lg">
      <div className="w-8 h-8 rounded-lg bg-white/10 flex items-center justify-center mb-2">
        <span className="text-base">⚡</span>
      </div>
      <NavBtn icon="🌐" onClick={() => navigate('/dashboard')} />
      <NavBtn icon="📋" onClick={() => navigate('/organization')} active />
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

// ─── BADGE ────────────────────────────────────────────────────────────────────
const planColor = {
  BASIC:      'bg-slate-100 text-slate-600 border-slate-200',
  PRO:        'bg-blue-50 text-blue-700 border-blue-200',
  ENTERPRISE: 'bg-indigo-50 text-indigo-700 border-indigo-200',
}
const typeColor = {
  INDIVIDUAL: 'bg-slate-100 text-slate-600 border-slate-200',
  SME:        'bg-emerald-50 text-emerald-700 border-emerald-200',
  ENTERPRISE: 'bg-amber-50 text-amber-700 border-amber-200',
}

function Badge({ label, cls }) {
  return (
    <span className={`inline-flex items-center text-xs font-semibold px-2.5 py-0.5 rounded-full border ${cls}`}>
      {label}
    </span>
  )
}

function StatusDot({ active }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-0.5 rounded-full border
      ${active ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-red-50 text-red-600 border-red-200'}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${active ? 'bg-emerald-500' : 'bg-red-400'}`} />
      {active ? 'Active' : 'Inactive'}
    </span>
  )
}

// ─── MODAL ────────────────────────────────────────────────────────────────────
function Modal({ title, onClose, children }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(15,23,42,0.5)', backdropFilter: 'blur(4px)' }}>
      <div className="bg-white rounded-2xl w-full max-w-md max-h-[90vh] overflow-y-auto shadow-xl border border-slate-200">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
          <h3 className="text-sm font-bold text-[#1a3a6b]">{title}</h3>
          <button onClick={onClose}
            className="w-7 h-7 rounded-lg flex items-center justify-center text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors cursor-pointer text-sm">
            ✕
          </button>
        </div>
        <div className="px-6 py-5">{children}</div>
      </div>
    </div>
  )
}

function Field({ label, children }) {
  return (
    <div>
      <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">{label}</label>
      {children}
    </div>
  )
}

function Input({ value, onChange, type = 'text', placeholder = '' }) {
  return (
    <input type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
      className="w-full bg-slate-50 text-slate-800 text-sm px-3 py-2.5 rounded-lg outline-none border border-slate-200 placeholder-slate-300 focus:border-blue-400 focus:bg-white transition-colors" />
  )
}

function Select({ value, onChange, options }) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      className="w-full bg-slate-50 text-slate-800 text-sm px-3 py-2.5 rounded-lg outline-none border border-slate-200 focus:border-blue-400 transition-colors cursor-pointer">
      {options.map(o => <option key={o} value={o}>{o}</option>)}
    </select>
  )
}

function SaveBtn({ onClick, saving, label = 'Save' }) {
  return (
    <div className="flex gap-3 mt-5 pt-4 border-t border-slate-100">
      <button onClick={onClick} disabled={saving}
        className="flex-1 py-2.5 rounded-lg text-sm font-semibold text-white bg-[#1a3a6b] hover:bg-[#15316b] disabled:opacity-50 transition-colors cursor-pointer shadow-sm">
        {saving ? 'Saving...' : label}
      </button>
    </div>
  )
}

// ─── ORG MODAL ────────────────────────────────────────────────────────────────
function OrgModal({ org, onClose, onSave }) {
  const isNew = !org
  const [form, setForm] = useState(isNew
    ? { org_name: '', org_type: 'SME', subscription_plan: 'BASIC', max_sites: 5, billing_email: '', billing_address: '', tax_id: '' }
    : { org_name: org.org_name, org_type: org.org_type, subscription_plan: org.subscription_plan, max_sites: org.max_sites, billing_email: org.billing_email || '', billing_address: org.billing_address || '', tax_id: org.tax_id || '', is_active: org.is_active })
  const [saving, setSaving] = useState(false)
  const set = k => v => setForm(f => ({ ...f, [k]: v }))

  const handleSave = async () => {
    setSaving(true)
    try {
      const url    = isNew ? '/api/organizations' : `/api/organizations/${org.org_id}`
      const method = isNew ? 'POST' : 'PUT'
      const res    = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) })
      const data   = await res.json()
      if (data.success) onSave(isNew ? { ...form, org_id: data.org_id, site_count: 0, user_count: 0, is_active: 1 } : { ...org, ...form })
    } catch {}
    setSaving(false)
  }

  return (
    <Modal title={isNew ? 'เพิ่ม Organization' : 'แก้ไข Organization'} onClose={onClose}>
      <div className="flex flex-col gap-4">
        <Field label="ชื่อ Organization"><Input value={form.org_name} onChange={set('org_name')} placeholder="Organization name" /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="ประเภท"><Select value={form.org_type} onChange={set('org_type')} options={['INDIVIDUAL','SME','ENTERPRISE']} /></Field>
          <Field label="แผน"><Select value={form.subscription_plan} onChange={set('subscription_plan')} options={['BASIC','PRO','ENTERPRISE']} /></Field>
        </div>
        <Field label="จำนวน Sites สูงสุด"><Input type="number" value={form.max_sites} onChange={v => set('max_sites')(+v)} /></Field>
        <Field label="Billing Email"><Input type="email" value={form.billing_email} onChange={set('billing_email')} placeholder="billing@example.com" /></Field>
        <Field label="ที่อยู่ Billing"><Input value={form.billing_address} onChange={set('billing_address')} placeholder="ที่อยู่" /></Field>
        <Field label="เลขผู้เสียภาษี"><Input value={form.tax_id} onChange={set('tax_id')} placeholder="Tax ID" /></Field>
        {!isNew && (
          <Field label="สถานะ">
            <div className="flex gap-2">
              {[{v:1,l:'Active'},{v:0,l:'Inactive'}].map(o => (
                <button key={o.v} onClick={() => set('is_active')(o.v)}
                  className={`flex-1 py-2 rounded-lg text-sm font-semibold border transition-all cursor-pointer
                    ${form.is_active === o.v
                      ? 'bg-[#1a3a6b] text-white border-[#1a3a6b]'
                      : 'bg-slate-50 text-slate-400 border-slate-200 hover:border-slate-300'}`}>
                  {o.l}
                </button>
              ))}
            </div>
          </Field>
        )}
      </div>
      <SaveBtn onClick={handleSave} saving={saving} label={isNew ? 'เพิ่ม Organization' : 'บันทึกการเปลี่ยนแปลง'} />
    </Modal>
  )
}

// ─── SITE MODAL ───────────────────────────────────────────────────────────────
function SiteModal({ orgId, site, onClose, onSave }) {
  const isNew = !site
  const [form, setForm] = useState(isNew
    ? { site_name: '', system_type: 'HYBRID', pv_capacity_kwp: '', battery_capacity_kwh: '', grid_capacity_kw: '', province: '' }
    : { site_name: site.site_name, system_type: site.system_type, pv_capacity_kwp: site.pv_capacity_kwp || '', battery_capacity_kwh: site.battery_capacity_kwh || '', grid_capacity_kw: site.grid_capacity_kw || '', province: site.province || '' })
  const [saving, setSaving] = useState(false)
  const set = k => v => setForm(f => ({ ...f, [k]: v }))

  const handleSave = async () => {
    setSaving(true)
    try {
      const url    = isNew ? `/api/organizations/${orgId}/sites` : `/api/organizations/${orgId}/sites/${site.site_id}`
      const method = isNew ? 'POST' : 'PUT'
      const res    = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) })
      const data   = await res.json()
      if (data.success) onSave(isNew ? { ...form, site_id: data.site_id, org_id: orgId, is_online: 0 } : { ...site, ...form })
    } catch {}
    setSaving(false)
  }

  return (
    <Modal title={isNew ? 'เพิ่ม Site' : 'แก้ไข Site'} onClose={onClose}>
      <div className="flex flex-col gap-4">
        <Field label="ชื่อ Site"><Input value={form.site_name} onChange={set('site_name')} placeholder="ชื่อ Site" /></Field>
        <Field label="ประเภทระบบ"><Select value={form.system_type} onChange={set('system_type')} options={['ON_GRID','OFF_GRID','HYBRID']} /></Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label="PV (kWp)"><Input type="number" value={form.pv_capacity_kwp} onChange={set('pv_capacity_kwp')} placeholder="0.00" /></Field>
          <Field label="Battery (kWh)"><Input type="number" value={form.battery_capacity_kwh} onChange={set('battery_capacity_kwh')} placeholder="0.00" /></Field>
          <Field label="Grid (kW)"><Input type="number" value={form.grid_capacity_kw} onChange={set('grid_capacity_kw')} placeholder="0.00" /></Field>
        </div>
        <Field label="จังหวัด"><Input value={form.province} onChange={set('province')} placeholder="จังหวัด" /></Field>
      </div>
      <SaveBtn onClick={handleSave} saving={saving} label={isNew ? 'เพิ่ม Site' : 'บันทึกการเปลี่ยนแปลง'} />
    </Modal>
  )
}

// ─── USER MODAL ───────────────────────────────────────────────────────────────
function UserModal({ orgId, user, onClose, onSave }) {
  const isNew = !user
  const [form, setForm] = useState(isNew
    ? { full_name: '', email: '', password: '', role: 'VIEWER' }
    : { full_name: user.full_name, email: user.email, role: user.role, is_active: user.is_active })
  const [saving, setSaving] = useState(false)
  const [error, setError]   = useState('')
  const set = k => v => setForm(f => ({ ...f, [k]: v }))

  const handleSave = async () => {
    setSaving(true); setError('')
    try {
      const url    = isNew ? `/api/organizations/${orgId}/users` : `/api/organizations/${orgId}/users/${user.user_id}`
      const method = isNew ? 'POST' : 'PUT'
      const res    = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) })
      const data   = await res.json()
      if (data.success) onSave(isNew ? { ...form, user_id: data.user_id, is_active: 1 } : { ...user, ...form })
      else setError(data.message)
    } catch { setError('เกิดข้อผิดพลาด') }
    setSaving(false)
  }

  return (
    <Modal title={isNew ? 'เพิ่ม User' : 'แก้ไข User'} onClose={onClose}>
      <div className="flex flex-col gap-4">
        <Field label="ชื่อ-นามสกุล"><Input value={form.full_name} onChange={set('full_name')} placeholder="ชื่อ-นามสกุล" /></Field>
        <Field label="Email"><Input type="email" value={form.email} onChange={set('email')} placeholder="email@example.com" /></Field>
        {isNew && <Field label="Password"><Input type="password" value={form.password} onChange={set('password')} placeholder="รหัสผ่าน" /></Field>}
        <Field label="Role"><Select value={form.role} onChange={set('role')} options={['VIEWER','ADMIN']} /></Field>
        {!isNew && (
          <Field label="สถานะ">
            <div className="flex gap-2">
              {[{v:1,l:'Active'},{v:0,l:'Inactive'}].map(o => (
                <button key={o.v} onClick={() => set('is_active')(o.v)}
                  className={`flex-1 py-2 rounded-lg text-sm font-semibold border transition-all cursor-pointer
                    ${form.is_active === o.v
                      ? 'bg-[#1a3a6b] text-white border-[#1a3a6b]'
                      : 'bg-slate-50 text-slate-400 border-slate-200 hover:border-slate-300'}`}>
                  {o.l}
                </button>
              ))}
            </div>
          </Field>
        )}
        {error && (
          <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 border border-red-200 px-3 py-2 rounded-lg">
            <span>⚠</span> {error}
          </div>
        )}
      </div>
      <SaveBtn onClick={handleSave} saving={saving} label={isNew ? 'เพิ่ม User' : 'บันทึกการเปลี่ยนแปลง'} />
    </Modal>
  )
}

// ─── ORG CARD ─────────────────────────────────────────────────────────────────
function OrgCard({ org, onEdit, onDelete, onReload }) {
  const [expanded, setExpanded]     = useState(false)
  const [tab, setTab]               = useState('sites')
  const [sites, setSites]           = useState([])
  const [users, setUsers]           = useState([])
  const [loadingDetail, setLoading] = useState(false)
  const [siteModal, setSiteModal]   = useState(null)
  const [userModal, setUserModal]   = useState(null)

  const loadDetail = async () => {
    setLoading(true)
    const [sRes, uRes] = await Promise.all([
      fetch(`/api/organizations/${org.org_id}/sites`).then(r => r.json()),
      fetch(`/api/organizations/${org.org_id}/users`).then(r => r.json()),
    ])
    if (sRes.success) setSites(sRes.sites)
    if (uRes.success) setUsers(uRes.users)
    setLoading(false)
  }

  const toggle = async () => {
    if (!expanded && sites.length === 0) await loadDetail()
    setExpanded(v => !v)
  }

  const handleDeleteSite = async (site_id) => {
    if (!confirm('ลบ site นี้?')) return
    const res = await fetch(`/api/organizations/${org.org_id}/sites/${site_id}`, { method: 'DELETE' })
    const data = await res.json()
    if (data.success) { setSites(s => s.filter(s => s.site_id !== site_id)); onReload() }
  }

  const handleDeleteUser = async (user_id) => {
    if (!confirm('ลบ user นี้?')) return
    const res = await fetch(`/api/organizations/${org.org_id}/users/${user_id}`, { method: 'DELETE' })
    const data = await res.json()
    if (data.success) { setUsers(u => u.filter(u => u.user_id !== user_id)); onReload() }
  }

  return (
    <>
      <div className={`bg-white rounded-xl border transition-all overflow-hidden
        ${expanded ? 'border-blue-200 shadow-md' : 'border-slate-200 shadow-sm hover:shadow-md hover:border-slate-300'}`}>

        {/* Card Header */}
        <div className="px-5 py-4 flex items-start justify-between gap-4">
          <div className="flex items-start gap-3 flex-1 min-w-0">
            <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 text-lg
              ${org.is_active ? 'bg-blue-50' : 'bg-slate-100'}`}>
              🏢
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap mb-1.5">
                <span className="text-sm font-bold text-slate-800 truncate">{org.org_name}</span>
                <StatusDot active={org.is_active} />
              </div>
              <div className="flex gap-2 flex-wrap items-center">
                <Badge label={org.org_type} cls={typeColor[org.org_type] || 'bg-slate-100 text-slate-600 border-slate-200'} />
                <Badge label={org.subscription_plan} cls={planColor[org.subscription_plan] || 'bg-slate-100 text-slate-600 border-slate-200'} />
                <span className="text-xs text-slate-300">{org.org_id}</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-4 shrink-0">
            {/* Stats */}
            <div className="hidden sm:flex gap-5">
              {[
                { v: org.site_count, l: 'Sites',   color: 'text-blue-600' },
                { v: org.user_count, l: 'Users',   color: 'text-indigo-600' },
                { v: org.max_sites,  l: 'Max',     color: 'text-slate-400' },
              ].map(s => (
                <div key={s.l} className="text-center">
                  <div className={`text-base font-bold ${s.color}`}>{s.v}</div>
                  <div className="text-xs text-slate-400">{s.l}</div>
                </div>
              ))}
            </div>
            {/* Actions */}
            <div className="flex items-center gap-1">
              <ActionBtn icon="✏️" onClick={() => onEdit(org)} title="แก้ไข" />
              <ActionBtn icon="🗑️" onClick={() => onDelete(org.org_id)} title="ลบ" danger />
              <button onClick={toggle}
                className="w-8 h-8 rounded-lg flex items-center justify-center text-xs text-slate-400 hover:text-[#1a3a6b] hover:bg-slate-100 border border-slate-200 transition-all cursor-pointer">
                {expanded ? '▲' : '▼'}
              </button>
            </div>
          </div>
        </div>

        {/* Billing row */}
        {(org.billing_email || org.tax_id || org.subscription_start) && (
          <div className="px-5 pb-3 flex gap-4 text-xs text-slate-400 border-t border-slate-50 pt-2.5 flex-wrap">
            {org.billing_email && <span className="flex items-center gap-1">📧 {org.billing_email}</span>}
            {org.subscription_start && (
              <span className="flex items-center gap-1">
                📅 {new Date(org.subscription_start).toLocaleDateString('th-TH')} → {new Date(org.subscription_end).toLocaleDateString('th-TH')}
              </span>
            )}
            {org.tax_id && <span className="flex items-center gap-1">🪙 {org.tax_id}</span>}
          </div>
        )}

        {/* Expandable Detail */}
        {expanded && (
          <div className="border-t border-slate-100 bg-slate-50">
            {/* Tabs */}
            <div className="flex border-b border-slate-200 px-4">
              {['sites', 'users'].map(t => (
                <button key={t} onClick={() => setTab(t)}
                  className={`px-4 py-2.5 text-xs font-semibold uppercase tracking-wider transition-all cursor-pointer border-b-2 -mb-px
                    ${tab === t ? 'text-[#1a3a6b] border-[#1a3a6b]' : 'text-slate-400 border-transparent hover:text-slate-600'}`}>
                  {t === 'sites' ? 'Sites' : 'Users'} ({t === 'sites' ? sites.length : users.length})
                </button>
              ))}
            </div>

            {loadingDetail ? (
              <div className="text-slate-400 text-sm text-center py-8">กำลังโหลด...</div>
            ) : (
              <div className="p-4">

                {/* ── SITES TAB ── */}
                {tab === 'sites' && (
                  <>
                    <div className="flex justify-end mb-3">
                      <button onClick={() => setSiteModal('new')}
                        className="text-xs font-semibold px-3 py-1.5 rounded-lg cursor-pointer text-white bg-[#1a3a6b] hover:bg-[#15316b] transition-colors shadow-sm">
                        + Add Site
                      </button>
                    </div>
                    {sites.length === 0
                      ? <div className="text-slate-400 text-sm text-center py-6">ยังไม่มี site</div>
                      : (
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                          {sites.map(s => (
                            <div key={s.site_id}
                              className="flex items-center justify-between bg-white rounded-xl px-4 py-3 border border-slate-200 hover:border-blue-200 transition-colors">
                              <div>
                                <div className="text-sm font-semibold text-slate-800">{s.site_name}</div>
                                <div className="text-xs text-slate-400 mt-0.5">{s.system_type} · {s.province}</div>
                                <div className="text-xs text-slate-300">{s.pv_capacity_kwp ?? '—'} kWp · {s.battery_capacity_kwh ?? '—'} kWh</div>
                              </div>
                              <div className="flex flex-col items-end gap-2">
                                <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border
                                  ${s.is_online ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-red-50 text-red-600 border-red-200'}`}>
                                  {s.is_online ? '● Online' : '● Offline'}
                                </span>
                                <div className="flex gap-1">
                                  <ActionBtn icon="✏️" onClick={() => setSiteModal(s)} small />
                                  <ActionBtn icon="🗑️" onClick={() => handleDeleteSite(s.site_id)} small danger />
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                  </>
                )}

                {/* ── USERS TAB ── */}
                {tab === 'users' && (
                  <>
                    <div className="flex justify-end mb-3">
                      <button onClick={() => setUserModal('new')}
                        className="text-xs font-semibold px-3 py-1.5 rounded-lg cursor-pointer text-white bg-[#1a3a6b] hover:bg-[#15316b] transition-colors shadow-sm">
                        + Add User
                      </button>
                    </div>
                    {users.length === 0
                      ? <div className="text-slate-400 text-sm text-center py-6">ยังไม่มี user</div>
                      : (
                        <div className="flex flex-col gap-2">
                          {users.map(u => (
                            <div key={u.user_id}
                              className="flex items-center justify-between bg-white rounded-xl px-4 py-3 border border-slate-200 hover:border-blue-200 transition-colors">
                              <div className="flex items-center gap-3">
                                <div className="w-8 h-8 rounded-full bg-blue-50 flex items-center justify-center text-[#1a3a6b] font-bold text-sm border border-blue-100">
                                  {u.full_name?.[0]?.toUpperCase() ?? '?'}
                                </div>
                                <div>
                                  <div className="text-sm font-semibold text-slate-800">{u.full_name}</div>
                                  <div className="text-xs text-slate-400">{u.email}</div>
                                </div>
                              </div>
                              <div className="flex items-center gap-2">
                                <Badge
                                  label={u.role}
                                  cls={u.role === 'ADMIN'
                                    ? 'bg-amber-50 text-amber-700 border-amber-200'
                                    : 'bg-blue-50 text-blue-700 border-blue-200'} />
                                <span className={`w-2 h-2 rounded-full ${u.is_active ? 'bg-emerald-400' : 'bg-red-400'}`} />
                                <ActionBtn icon="✏️" onClick={() => setUserModal(u)} small />
                                <ActionBtn icon="🗑️" onClick={() => handleDeleteUser(u.user_id)} small danger />
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                  </>
                )}

              </div>
            )}
          </div>
        )}
      </div>

      {siteModal && (
        <SiteModal orgId={org.org_id} site={siteModal === 'new' ? null : siteModal} onClose={() => setSiteModal(null)}
          onSave={s => { setSites(prev => siteModal === 'new' ? [...prev, s] : prev.map(x => x.site_id === s.site_id ? s : x)); setSiteModal(null); onReload() }} />
      )}
      {userModal && (
        <UserModal orgId={org.org_id} user={userModal === 'new' ? null : userModal} onClose={() => setUserModal(null)}
          onSave={u => { setUsers(prev => userModal === 'new' ? [...prev, u] : prev.map(x => x.user_id === u.user_id ? u : x)); setUserModal(null); onReload() }} />
      )}
    </>
  )
}

// ─── ACTION BUTTON ────────────────────────────────────────────────────────────
function ActionBtn({ icon, onClick, title, danger, small }) {
  return (
    <button onClick={onClick} title={title}
      className={`rounded-lg flex items-center justify-center border transition-all cursor-pointer
        ${small ? 'w-7 h-7 text-xs' : 'w-8 h-8 text-sm'}
        ${danger
          ? 'text-slate-300 hover:text-red-600 hover:bg-red-50 border-slate-200 hover:border-red-200'
          : 'text-slate-300 hover:text-[#1a3a6b] hover:bg-blue-50 border-slate-200 hover:border-blue-200'}`}>
      {icon}
    </button>
  )
}

// ─── MAIN ─────────────────────────────────────────────────────────────────────
function OrganizationList({ user }) {
  const navigate = useNavigate()
  const [orgs, setOrgs]         = useState([])
  const [loading, setLoading]   = useState(true)
  const [orgModal, setOrgModal] = useState(null)
  const [search, setSearch]     = useState('')

  const loadOrgs = () => {
    setLoading(true)
    fetch('/api/organizations').then(r => r.json())
      .then(d => { if (d.success) setOrgs(d.organizations) })
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadOrgs() }, [])

  const handleDelete = async (org_id) => {
    if (!confirm('ปิดใช้งาน organization นี้?')) return
    const res  = await fetch(`/api/organizations/${org_id}`, { method: 'DELETE' })
    const data = await res.json()
    if (data.success) loadOrgs()
  }

  const filtered = orgs.filter(o =>
    o.org_name.toLowerCase().includes(search.toLowerCase()) || o.org_id.includes(search.toLowerCase()))

  return (
    <div className="h-screen bg-slate-50 flex overflow-hidden" style={{ fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif" }}>
      <Sidebar navigate={navigate} />

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

        {/* ── TOP BAR ── */}
        <div className="bg-white border-b border-slate-200 px-5 py-3 flex items-center justify-between gap-4 shrink-0">
          <div>
            <div className="text-xs text-slate-400 font-medium uppercase tracking-widest">GridMind Platform</div>
            <h1 className="text-base font-bold text-[#1a3a6b] leading-tight">Organizations</h1>
          </div>
          <div className="flex items-center gap-3">
            {/* Search */}
            <div className="relative">
              <input value={search} onChange={e => setSearch(e.target.value)} placeholder="ค้นหา..."
                className="bg-slate-50 border border-slate-200 text-slate-700 text-sm px-4 py-1.5 rounded-lg outline-none placeholder-slate-300 focus:border-blue-400 focus:bg-white transition-colors w-44" />
              {search && (
                <button onClick={() => setSearch('')}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-500 cursor-pointer text-xs">
                  ✕
                </button>
              )}
            </div>
            {/* Summary */}
            <div className="hidden sm:flex items-center gap-1 text-xs text-slate-400 bg-slate-50 border border-slate-200 px-3 py-1.5 rounded-lg">
              <span className="font-semibold text-[#1a3a6b]">{orgs.length}</span> orgs ·
              <span className="font-semibold text-[#1a3a6b]">{orgs.reduce((a, o) => a + +o.site_count, 0)}</span> sites
            </div>
            {/* Add */}
            <button onClick={() => setOrgModal('new')}
              className="text-xs font-semibold px-4 py-2 rounded-lg cursor-pointer text-white bg-[#1a3a6b] hover:bg-[#15316b] transition-colors shadow-sm">
              + Add Org
            </button>
          </div>
        </div>

        {/* ── LIST ── */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="flex items-center justify-center h-full text-slate-400 text-sm">กำลังโหลด...</div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-2 text-slate-300">
              <span className="text-3xl">🏢</span>
              <span className="text-sm">ไม่พบ organization</span>
            </div>
          ) : (
            <div className="flex flex-col gap-3 max-w-4xl mx-auto">
              {filtered.map(org => (
                <OrgCard key={org.org_id} org={org} onEdit={setOrgModal} onDelete={handleDelete} onReload={loadOrgs} />
              ))}
            </div>
          )}
        </div>
      </div>

      {orgModal && (
        <OrgModal org={orgModal === 'new' ? null : orgModal} onClose={() => setOrgModal(null)}
          onSave={o => {
            orgModal === 'new' ? setOrgs(p => [o, ...p]) : setOrgs(p => p.map(x => x.org_id === o.org_id ? o : x))
            setOrgModal(null)
          }} />
      )}
    </div>
  )
}

export default OrganizationList