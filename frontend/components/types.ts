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

type ValidationDetail = { loc?: (string | number)[]; msg?: string };

function errorMessage(detail: unknown, status: number): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const labels: Record<string, string> = { name: 'Domain name', type: 'Zone type', comment: 'Description', tags: 'Tags', vpc_region: 'VPC region', vpc_id: 'VPC ID' };
    const messages = (detail as ValidationDetail[]).map(issue => {
      const fields = (issue.loc || []).filter(part => !['body', 'query', 'path'].includes(String(part)));
      const field = fields.map(part => labels[String(part)] || String(part)).join(' › ');
      const message = (issue.msg || 'Invalid value').replace(/^Value error, /, '');
      return field ? `${field}: ${message}` : message;
    });
    if (messages.length) return messages.join('; ');
  }
  if (detail && typeof detail === 'object' && 'errors' in detail) {
    const errors = (detail as { errors: unknown }).errors;
    if (Array.isArray(errors)) return errors.map(String).join('; ');
  }
  return `Request failed (${status})`;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: options.body instanceof FormData ? options.headers : { 'Content-Type': 'application/json', ...options.headers },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(errorMessage(data.detail, response.status));
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
