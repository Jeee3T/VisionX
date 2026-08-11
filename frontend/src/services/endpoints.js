import api from './api'

export const authApi = {
  register: (payload) => api.post('/auth/register', payload),
  login: (payload) => api.post('/auth/login', payload),
  me: () => api.get('/auth/me'),
  logout: () => api.post('/auth/logout'),
}

export const userApi = {
  get: () => api.get('/users/me'),
  update: (payload) => api.put('/users/me', payload),
  changePassword: (payload) => api.put('/users/me/password', payload),
}

export const presentationApi = {
  list: (search = '') => api.get('/presentations', { params: search ? { search } : {} }),
  get: (id) => api.get(`/presentations/${id}`),
  upload: (formData, onProgress) =>
    api.post('/presentations', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
      onUploadProgress: (event) => {
        if (onProgress && event.total) onProgress(Math.round((event.loaded * 100) / event.total))
      },
    }),
  update: (id, payload) => api.put(`/presentations/${id}`, payload),
  remove: (id) => api.delete(`/presentations/${id}`),
}

export const gestureApi = {
  get: () => api.get('/gestures/preferences'),
  update: (payload) => api.put('/gestures/preferences', payload),
}

export const sessionApi = {
  create: (presentationId) => api.post('/sessions', { presentationId }),
  list: (limit = 50) => api.get('/sessions', { params: { limit } }),
  get: (id) => api.get(`/sessions/${id}`),
  update: (id, payload) => api.put(`/sessions/${id}`, payload),
  complete: (id, summary = {}) => api.post(`/sessions/${id}/complete`, summary),
}

export const annotationApi = {
  create: (payload) => api.post('/annotations', payload),
  forSlide: (presentationId, slide) => api.get(`/annotations/${presentationId}/${slide}`),
  forPresentation: (presentationId) => api.get(`/annotations/presentation/${presentationId}`),
  remove: (id) => api.delete(`/annotations/${id}`),
  clearSlide: (presentationId, slide) => api.delete(`/annotations/${presentationId}/${slide}`),
}

export const analyticsApi = {
  dashboard: () => api.get('/analytics/dashboard'),
  presentations: () => api.get('/analytics/presentations'),
  gestures: () => api.get('/analytics/gestures'),
}

export const engineApi = {
  start: (sessionId, options = {}) => api.post('/engine/start', { sessionId, options }),
  stop: () => api.post('/engine/stop'),
  status: () => api.get('/engine/status'),
  command: (command) => api.post('/engine/command', { command }),
  setSlide: (slide) => api.post('/engine/slide', { slide }),
  cameras: () => api.get('/engine/cameras'),
}
