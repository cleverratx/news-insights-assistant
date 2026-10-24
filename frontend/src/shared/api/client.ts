import { withPosition } from './position-context';

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return 'Сервер не отвечает. Проверьте подключение и повторите.';
}

async function send<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail =
      body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string'
        ? body.detail
        : `Ошибка запроса (${response.status})`;
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return send<T>(path, { signal });
}

export function apiPost<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const role =
    body && typeof body === 'object' && 'role' in body && typeof body.role === 'string'
      ? body.role
      : null;
  return send<T>(withPosition(path, role), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
}

export function apiPatch<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return send<T>(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
}
