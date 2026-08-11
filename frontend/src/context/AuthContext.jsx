import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { authApi } from '../services/endpoints'
import { clearToken, getToken, setToken } from '../services/api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(Boolean(getToken()))

  useEffect(() => {
    if (!getToken()) {
      setLoading(false)
      return
    }
    authApi
      .me()
      .then((response) => setUser(response.data.user))
      .catch(() => {
        clearToken()
        setUser(null)
      })
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (credentials) => {
    const response = await authApi.login(credentials)
    setToken(response.data.token)
    setUser(response.data.user)
    return response.data.user
  }, [])

  const register = useCallback(async (payload) => {
    const response = await authApi.register(payload)
    setToken(response.data.token)
    setUser(response.data.user)
    return response.data.user
  }, [])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch {
      // A logout must always succeed locally, even if the API is unreachable.
    }
    clearToken()
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, setUser, loading, login, register, logout, isAuthenticated: Boolean(user) }),
    [user, loading, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}
