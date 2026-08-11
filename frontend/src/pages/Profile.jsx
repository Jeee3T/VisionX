import { useState } from 'react'
import { KeyRound, Save, ShieldCheck } from 'lucide-react'
import PageHeader from '../components/PageHeader'
import { userApi } from '../services/endpoints'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import { formatDate, initials } from '../utils/format'

export default function Profile() {
  const { user, setUser } = useAuth()
  const toast = useToast()
  const [name, setName] = useState(user?.name || '')
  const [savingProfile, setSavingProfile] = useState(false)
  const [passwords, setPasswords] = useState({ currentPassword: '', newPassword: '', confirm: '' })
  const [savingPassword, setSavingPassword] = useState(false)
  const [passwordError, setPasswordError] = useState('')

  const saveProfile = async (event) => {
    event.preventDefault()
    setSavingProfile(true)
    try {
      const response = await userApi.update({ name })
      setUser(response.data.user)
      toast.success('Profile updated.')
    } catch (err) {
      toast.error(err.message)
    } finally {
      setSavingProfile(false)
    }
  }

  const savePassword = async (event) => {
    event.preventDefault()
    setPasswordError('')
    if (passwords.newPassword.length < 8) return setPasswordError('New password must be at least 8 characters.')
    if (passwords.newPassword !== passwords.confirm) return setPasswordError('The two new passwords do not match.')

    setSavingPassword(true)
    try {
      await userApi.changePassword({
        currentPassword: passwords.currentPassword,
        newPassword: passwords.newPassword,
      })
      setPasswords({ currentPassword: '', newPassword: '', confirm: '' })
      toast.success('Password updated.')
    } catch (err) {
      setPasswordError(err.message)
    } finally {
      setSavingPassword(false)
    }
  }

  return (
    <div className="animate-fade-in">
      <PageHeader title="Profile" subtitle="Your account details and password." />

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="card h-fit p-6 text-center">
          <span className="mx-auto flex h-20 w-20 items-center justify-center rounded-3xl bg-brand-gradient text-2xl font-semibold text-white shadow-lift">
            {initials(user?.name)}
          </span>
          <h2 className="mt-4 text-base font-semibold text-ink-900">{user?.name}</h2>
          <p className="text-sm text-ink-500">{user?.email}</p>
          <p className="mt-3 text-xs text-ink-400">Member since {formatDate(user?.createdAt)}</p>
        </div>

        <div className="space-y-5 lg:col-span-2">
          <form onSubmit={saveProfile} className="card p-5">
            <h2 className="text-sm font-semibold text-ink-800">Account details</h2>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="name">Full name</label>
                <input id="name" className="input" value={name} onChange={(e) => setName(e.target.value)} required />
              </div>
              <div>
                <label className="label" htmlFor="email">Email</label>
                <input id="email" className="input bg-ink-50 text-ink-500" value={user?.email || ''} disabled />
                <p className="mt-1.5 text-xs text-ink-400">Your email is your sign-in identity and cannot be changed here.</p>
              </div>
            </div>
            <button type="submit" disabled={savingProfile} className="btn-primary mt-5">
              <Save size={16} /> {savingProfile ? 'Saving…' : 'Save changes'}
            </button>
          </form>

          <form onSubmit={savePassword} className="card p-5">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink-800">
              <KeyRound size={16} className="text-ink-400" /> Change password
            </h2>

            {passwordError && (
              <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">
                {passwordError}
              </div>
            )}

            <div className="mt-4 grid gap-4 sm:grid-cols-3">
              <div>
                <label className="label" htmlFor="current">Current</label>
                <input
                  id="current"
                  type="password"
                  className="input"
                  required
                  value={passwords.currentPassword}
                  onChange={(e) => setPasswords({ ...passwords, currentPassword: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="new">New</label>
                <input
                  id="new"
                  type="password"
                  className="input"
                  required
                  value={passwords.newPassword}
                  onChange={(e) => setPasswords({ ...passwords, newPassword: e.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="confirmPassword">Confirm</label>
                <input
                  id="confirmPassword"
                  type="password"
                  className="input"
                  required
                  value={passwords.confirm}
                  onChange={(e) => setPasswords({ ...passwords, confirm: e.target.value })}
                />
              </div>
            </div>

            <div className="mt-5 flex items-center gap-3">
              <button type="submit" disabled={savingPassword} className="btn-primary">
                <ShieldCheck size={16} /> {savingPassword ? 'Updating…' : 'Update password'}
              </button>
              <p className="text-xs text-ink-400">Passwords are stored as bcrypt hashes — never in plain text.</p>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
