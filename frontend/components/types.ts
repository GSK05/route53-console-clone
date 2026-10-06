export type Zone = {
  id: string; name: string; type: 'public' | 'private'; comment: string;
  vpc_region: string | null; vpc_id: string | null; name_servers: string[];
  created_at: string; updated_at: string; record_count: number;
  tags: { key: string; value: string }[];
};
export type RecordSet = {
  id: string; zone_id: string; name: string; type: string; ttl: number;
  values: string[]; routing_policy: string; is_default: boolean;
  created_at: string; updated_at: string;
};
export type PageResult<T> = { items: T[]; total: number; page: number; page_size: number };

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: options.body instanceof FormData ? options.headers : { 'Content-Type': 'application/json', ...options.headers },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.errors?.join('; ') || `Request failed (${response.status})`);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
