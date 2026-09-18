export class ApiError extends Error {
  constructor(public status: number, message: string, public code = '', public fields: Record<string, string> = {}) { super(message) }
}
export async function api<T>(path: string, method = 'GET', body?: unknown, csrf = ''): Promise<T> {
  const response = await fetch(path, {
    method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-QuotePilot-Request': '1', 'X-CSRF-Token': csrf },
    ...(method !== 'GET' ? { body: JSON.stringify(body ?? {}) } : {}),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new ApiError(response.status, typeof error.message === 'string' ? error.message : 'Service unavailable. Try again.', error.code, error.fields ?? {})
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>
}
