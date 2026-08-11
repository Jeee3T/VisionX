import axios from 'axios'

const TOKEN_KEY = 'visionx.token'

export const getToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const setToken = (token) => localStorage.setItem(TOKEN_KEY, token)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

// In dev, Vite proxies /api to Flask. VITE_API_URL overrides it for a deployed build.
export const API_BASE = import.meta.env.VITE_API_URL || '/api'

const api = axios.create({ baseURL: API_BASE, timeout: 30000 })

api.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const envelope = error.response?.data?.error
    const status = error.response?.status

    if (status === 401 && !String(error.config?.url || '').includes('/auth/')) {
      clearToken()
      if (!window.location.pathname.startsWith('/login')) {
        window.location.assign('/login?expired=1')
      }
    }

    return Promise.reject({
      code: envelope?.code || (error.code === 'ECONNABORTED' ? 'TIMEOUT' : 'NETWORK_ERROR'),
      message:
        envelope?.message ||
        (error.request && !error.response
          ? 'Cannot reach the VisionX server. Make sure the Flask API is running on port 5000.'
          : 'Something went wrong. Please try again.'),
      status,
    })
  },
)

/** Build an absolute URL for streaming endpoints (EventSource / <img src>). */
export const streamUrl = (path, params = {}) => {
  const query = new URLSearchParams({ token: getToken(), ...params }).toString()
  return `${API_BASE}${path}?${query}`
}

export default api
