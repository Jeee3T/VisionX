import { Link, Navigate, Route, Routes } from 'react-router-dom'
import ProtectedRoute, { PublicOnlyRoute } from './components/ProtectedRoute'
import AppLayout from './layouts/AppLayout'
import Landing from './pages/Landing'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import Library from './pages/Library'
import UploadPresentation from './pages/UploadPresentation'
import PresentationDetails from './pages/PresentationDetails'
import Session from './pages/Session'
import GestureSettings from './pages/GestureSettings'
import GestureTraining from './pages/GestureTraining'
import VoiceAssistant from './pages/VoiceAssistant'
import History from './pages/History'
import Analytics from './pages/Analytics'
import Profile from './pages/Profile'

function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
      <p className="text-5xl font-semibold text-ink-900">404</p>
      <p className="max-w-sm text-sm text-ink-500">That page does not exist in VisionX.</p>
      <Link to="/dashboard" className="btn-primary">
        Back to dashboard
      </Link>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />

      <Route element={<PublicOnlyRoute />}>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
      </Route>

      <Route element={<ProtectedRoute />}>
        {/* The session screen owns the whole viewport - no app chrome around it. */}
        <Route path="/session/new" element={<Session />} />

        <Route element={<AppLayout />}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/presentations" element={<Library />} />
          <Route path="/presentations/:id" element={<PresentationDetails />} />
          <Route path="/upload" element={<UploadPresentation />} />
          <Route path="/gestures" element={<GestureSettings />} />
          <Route path="/gestures/train" element={<GestureTraining />} />
          <Route path="/voice" element={<VoiceAssistant />} />
          <Route path="/history" element={<History />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/profile" element={<Profile />} />
        </Route>
      </Route>

      <Route path="/session" element={<Navigate to="/session/new" replace />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}
