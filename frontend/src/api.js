export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

async function request(path, options = {}) {
  let res
  try {
    res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
  } catch {
    throw new ApiError(0, '서버에 연결할 수 없습니다. 백엔드가 실행 중인지 확인해 주세요.')
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new ApiError(res.status, data.detail || '요청에 실패했습니다.')
  }
  return data
}

export const createSession = () => request('/api/sessions', { method: 'POST' })

export const getSession = (sessionId) => request(`/api/sessions/${sessionId}`)

export const sendMessage = (sessionId, content, requestId) =>
  request(`/api/sessions/${sessionId}/messages`, {
    method: 'POST',
    body: JSON.stringify({ content, request_id: requestId }),
  })

export const summarize = (sessionId) =>
  request(`/api/sessions/${sessionId}/summary`, { method: 'POST' })
