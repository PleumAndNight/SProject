import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

function Login({ setUser }) {
  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [error, setError]       = useState('')
  const [loading, setLoading]   = useState(false)
  const [mounted, setMounted]   = useState(false)
  const [showPass, setShowPass] = useState(false)
  const navigate = useNavigate()

  useEffect(() => { setTimeout(() => setMounted(true), 60) }, [])

  const handleLogin = async () => {
    if (!email || !password) { setError('กรุณากรอกข้อมูลให้ครบ'); return }
    setLoading(true); setError('')
    try {
      const res  = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      const data = await res.json()
      if (res.ok && data.success) {
        // setUser trigger ให้ App.jsx re-render แล้ว <Navigate> ใน route จะ redirect ตาม role อัตโนมัติ
        // ไม่ navigate เองเพื่อป้องกัน race condition ระหว่าง state update กับ route guard
        setUser({
          user_id:  data.user.user_id,
          username: data.user.full_name,
          email:    data.user.email,
          role:     data.user.role,
        })
      } else {
        setError(data.message || 'อีเมลหรือรหัสผ่านไม่ถูกต้อง')
      }
    } catch {
      setError('ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์ได้')
    } finally {
      setLoading(false)
    }
  }

  const handleKey = e => { if (e.key === 'Enter') handleLogin() }

  return (
    <div className="min-h-screen flex overflow-hidden bg-slate-50"
      style={{ fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif" }}>

      {/* ── LEFT PANEL — Branding ── */}
      <div className="hidden lg:flex flex-col justify-between w-[45%] bg-[#1a3a6b] p-14 relative overflow-hidden shrink-0">

        {/* Background pattern */}
        <div className="absolute inset-0 pointer-events-none overflow-hidden">
          {/* Grid lines */}
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={`h${i}`} className="absolute w-full border-t border-white/[0.04]"
              style={{ top: `${(i + 1) * 11.11}%` }} />
          ))}
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={`v${i}`} className="absolute h-full border-l border-white/[0.04]"
              style={{ left: `${(i + 1) * 11.11}%` }} />
          ))}
          {/* Subtle glow */}
          <div className="absolute w-[500px] h-[500px] rounded-full -bottom-32 -right-32 opacity-10"
            style={{ background: 'radial-gradient(circle, #60a5fa, transparent 70%)' }} />
          <div className="absolute w-72 h-72 rounded-full top-10 -left-20 opacity-10"
            style={{ background: 'radial-gradient(circle, #93c5fd, transparent 70%)' }} />
        </div>

        {/* Logo */}
        <div className="relative flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-white/10 flex items-center justify-center border border-white/20">
            <span className="text-lg">⚡</span>
          </div>
          <div>
            <div className="text-white font-bold text-base leading-none">GridMind</div>
            <div className="text-blue-200/60 text-xs tracking-widest uppercase">AI Platform</div>
          </div>
        </div>

        {/* Hero text */}
        <div className="relative">
          <div className="text-blue-200/40 text-xs tracking-[0.25em] uppercase font-medium mb-5">
            Energy Intelligence Platform
          </div>
          <h1 className="text-white font-black leading-none mb-6"
            style={{ fontSize: 'clamp(2.2rem, 4vw, 3.5rem)', letterSpacing: '-0.02em' }}>
            MONITOR.<br />
            <span className="text-blue-300">ANALYZE.</span><br />
            OPTIMIZE.
          </h1>
          <p className="text-blue-100/40 text-sm leading-relaxed max-w-xs">
            ระบบบริหารจัดการพลังงานแสงอาทิตย์แบบ real-time พร้อม AI forecasting ครอบคลุมทุก site
          </p>
        </div>

        {/* Stats */}
        <div className="relative flex gap-8 pt-6 border-t border-white/10">
          {[
            
          ].map(s => (
            <div key={s.label}>
              <div className="text-white font-bold text-lg">{s.value}</div>
              <div className="text-blue-200/40 text-xs mt-0.5">{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── RIGHT PANEL — Login Form ── */}
      <div className="flex-1 flex items-center justify-center px-8 relative">

        {/* Subtle background */}
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute w-96 h-96 rounded-full top-0 right-0 opacity-40"
            style={{ background: 'radial-gradient(circle, #dbeafe, transparent 70%)' }} />
          <div className="absolute w-64 h-64 rounded-full bottom-0 left-0 opacity-30"
            style={{ background: 'radial-gradient(circle, #e0e7ff, transparent 70%)' }} />
        </div>

        <div
          className="w-full max-w-sm relative"
          style={{
            opacity: mounted ? 1 : 0,
            transform: mounted ? 'translateY(0)' : 'translateY(16px)',
            transition: 'opacity 0.5s ease, transform 0.5s ease',
          }}
        >
          {/* Mobile logo */}
          <div className="lg:hidden flex items-center gap-2 mb-10">
            <div className="w-8 h-8 rounded-lg bg-[#1a3a6b] flex items-center justify-center">
              <span className="text-sm">⚡</span>
            </div>
            <div>
              <div className="text-[#1a3a6b] font-bold text-sm leading-none">GridMind</div>
              <div className="text-slate-400 text-xs">AI Platform</div>
            </div>
          </div>

          {/* Form card */}
          <div className="bg-white rounded-2xl border border-slate-200 shadow-lg p-8">

            <div className="mb-7">
              <h2 className="text-xl font-bold text-[#1a3a6b]">เข้าสู่ระบบ</h2>
              <p className="text-slate-400 text-sm mt-1">กรอกข้อมูลเพื่อเข้าใช้งานระบบ</p>
            </div>

            <div className="flex flex-col gap-4">

              {/* Email */}
              <div>
                <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
                  Email
                </label>
                <input
                  type="text"
                  value={email}
                  onChange={e => { setEmail(e.target.value); setError('') }}
                  onKeyDown={handleKey}
                  placeholder="your@email.com"
                  className="w-full bg-slate-50 text-slate-800 text-sm px-4 py-2.5 rounded-lg border border-slate-200 outline-none placeholder-slate-300 focus:border-blue-400 focus:bg-white transition-colors"
                />
              </div>

              {/* Password */}
              <div>
                <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
                  Password
                </label>
                <div className="relative">
                  <input
                    type={showPass ? 'text' : 'password'}
                    value={password}
                    onChange={e => { setPassword(e.target.value); setError('') }}
                    onKeyDown={handleKey}
                    placeholder="••••••••"
                    className="w-full bg-slate-50 text-slate-800 text-sm px-4 py-2.5 pr-10 rounded-lg border border-slate-200 outline-none placeholder-slate-300 focus:border-blue-400 focus:bg-white transition-colors"
                  />
                  <button
                    onClick={() => setShowPass(v => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-300 hover:text-slate-500 transition-colors text-xs cursor-pointer">
                    {showPass ? '🙈' : '👁'}
                  </button>
                </div>
              </div>

              {/* Error */}
              {error && (
                <div className="flex items-center gap-2 text-xs text-red-600 bg-red-50 border border-red-200 px-3 py-2.5 rounded-lg">
                  <span>⚠</span> {error}
                </div>
              )}

              {/* Submit */}
              <button
                onClick={handleLogin}
                disabled={loading}
                className="w-full py-2.5 rounded-lg text-sm font-semibold text-white bg-[#1a3a6b] hover:bg-[#15316b] disabled:opacity-60 transition-colors cursor-pointer shadow-sm mt-1"
              >
                {loading ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    กำลังตรวจสอบ...
                  </span>
                ) : 'เข้าสู่ระบบ →'}
              </button>
            </div>
          </div>

          <div className="mt-5 flex items-center justify-between text-slate-300 text-xs px-1">
            <span>GridMind AI Platform</span>
            <span>v1.0.0</span>
          </div>
        </div>
      </div>
    </div>
  )
}

export default Login