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
  startVoiceOnly: (sessionId, options = {}) => api.post('/engine/start-voice', { sessionId, options }),
  stop: () => api.post('/engine/stop'),
  status: () => api.get('/engine/status'),
  command: (command, parameters = {}) => api.post('/engine/command', { command, parameters }),
  commands: () => api.get('/engine/commands'),
  setSlide: (slide) => api.post('/engine/slide', { slide }),
  cameras: () => api.get('/engine/cameras'),
}

/** Personalized gesture recognition: settings, enrolment, training, deletion. */
export const personalizationApi = {
  get: () => api.get('/personalization/'),
  update: (payload) => api.put('/personalization/', payload),

  plan: () => api.get('/personalization/enrollment'),
  startCamera: (options = {}) => api.post('/personalization/enrollment/camera/start', { options }),
  stopCamera: () => api.post('/personalization/enrollment/camera/stop'),
  startRecording: (label, frames) =>
    api.post('/personalization/enrollment/recording/start', { label, frames }),
  recordingStatus: () => api.get('/personalization/enrollment/recording'),
  finishRecording: () => api.post('/personalization/enrollment/recording/finish'),
  cancelRecording: () => api.post('/personalization/enrollment/recording/cancel'),

  // Training runs on a server-side worker thread; this call returns immediately.
  train: (seed = 42) => api.post('/personalization/train', { seed }),
  trainStatus: () => api.get('/personalization/train/status'),

  deleteModel: () => api.delete('/personalization/model'),
  deleteRecordings: () => api.delete('/personalization/recordings'),
  deleteAll: () => api.delete('/personalization/'),
}

/** Voice assistant: status, transcription, interpretation, history. */
export const voiceApi = {
  status: () => api.get('/voice/status'),
  commands: () => api.get('/voice/commands'),
  interpret: (text, options = {}) => api.post('/voice/interpret', { text, ...options }),
  confirm: (text, sessionId) => api.post('/voice/confirm', { text, sessionId }),
  history: (limit = 50, sessionId) =>
    api.get('/voice/history', { params: { limit, ...(sessionId ? { sessionId } : {}) } }),
  clearHistory: () => api.delete('/voice/history'),

  utterance: (blob, { sessionId, execute = true } = {}) => {
    const form = new FormData()
    // Transcribed on the server and discarded - the audio is never stored.
    form.append('audio', blob, 'utterance.webm')
    form.append('execute', execute ? '1' : '0')
    if (sessionId) form.append('sessionId', sessionId)
    return api.post('/voice/utterance', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000,
    })
  },

  // --- continuous listening ("Vision <command> OK") ------------------------
  // One short segment from the always-on microphone. Most segments are ordinary
  // speech and come back with action IDLE, having done nothing at all.
  stream: (blob, { sessionId, execute = true } = {}) => {
    const form = new FormData()
    form.append('audio', blob, 'segment.webm')
    form.append('execute', execute ? '1' : '0')
    if (sessionId) form.append('sessionId', sessionId)
    return api.post('/voice/stream', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000,
    })
  },
  streamText: (text, options = {}) => api.post('/voice/stream/text', { text, ...options }),
  wake: () => api.get('/voice/wake'),
  resetWake: () => api.post('/voice/wake/reset'),
}
