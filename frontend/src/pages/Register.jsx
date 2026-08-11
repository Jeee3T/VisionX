import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Check, Loader2, UserPlus } from 'lucide-react'
import AuthLayout from '../layouts/AuthLayout'
import { useAuth } from '../context/AuthContext'

export default function Register() {
  const { register } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const longEnough = form.password.length >= 8
  const matches = form.password && form.password === form.confirm

  const submit = async (event) => {
    event.preventDefault()
    setError('')
    if (!longEnough) return setError('Password must be at least 8 characters long.')
    if (!matches) return setError('The two passwords do not match.')

    setBusy(true)
    try {
      await register({ name: form.name, email: form.email, password: form.password })
      navigate('/dashboard', { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthLayout
      title="Create your account"
      subtitle="Your gesture bindings and session history live here."
      footer={
        <>
          Already registered?{' '}
          <Link to="/login" className="font-medium text-brand-600 hover:text-brand-700">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
        )}

        <div>
          <label className="label" htmlFor="name">Full name</label>
          <input
            id="name"
            required
            className="input"
            placeholder="Prasanjeet Panda"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>

        <div>
          <label className="label" htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            className="input"
            placeholder="you@example.com"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="new-password"
              required
              className="input"
              placeholder="8+ characters"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
          </div>
          <div>
            <label className="label" htmlFor="confirm">Confirm</label>
            <input
              id="confirm"
              type="password"
              autoComplete="new-password"
              required
              className="input"
              placeholder="Repeat password"
              value={form.confirm}
              onChange={(e) => setForm({ ...form, confirm: e.target.value })}
            />
          </div>
        </div>

        <ul className="space-y-1.5 text-xs">
          <li className={`flex items-center gap-1.5 ${longEnough ? 'text-emerald-600' : 'text-ink-400'}`}>
            <Check size={13} /> At least 8 characters
          </li>
          <li className={`flex items-center gap-1.5 ${matches ? 'text-emerald-600' : 'text-ink-400'}`}>
            <Check size={13} /> Both passwords match
          </li>
        </ul>

        <button type="submit" disabled={busy} className="btn-primary w-full py-3">
          {busy ? <Loader2 size={17} className="animate-spin" /> : <UserPlus size={17} />}
          {busy ? 'Creating account…' : 'Create account'}
        </button>
      </form>
    </AuthLayout>
  )
}
