import type { Session } from './types';

export function readSession(): Session | null {
  try { return JSON.parse(sessionStorage.getItem('jalani-session') || 'null'); }
  catch { return null; }
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const session = readSession();
  const response = await fetch(`/api${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...(session ? { Authorization: `Bearer ${session.access_token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('session-expired'));
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status})`);
  }
  return data as T;
}

export const number = (value?: number | null, digits = 0) => value === undefined || value === null ? '—' : new Intl.NumberFormat('en', { maximumFractionDigits: digits }).format(value);
export const percent = (value?: number | null) => value === undefined || value === null ? '—' : `${number(value * 100, 1)}%`;
export const label = (value: string) => value.replaceAll('_', ' ').replaceAll('-', ' ');
