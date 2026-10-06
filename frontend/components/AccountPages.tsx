'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, ApiError, User } from './types';

type Identity = { id: string; name: string; policy: string; created_at?: string };
type Organization = { organization: { id: string; name: string } | null; accounts: { id: string; name: string; email: string }[] };
type Billing = { period: string; currency: string; hosted_zones: number; record_sets: number; dns_queries: number; illustrative_total: number; payment_status: string };
const POLICIES = ['AdministratorAccess', 'AmazonRoute53ReadOnlyAccess'];

export default function AccountPages({ section, user, onUserUpdate, onExpired }: { section: string; user: User; onUserUpdate: (user: User) => void; onExpired: () => void }) {
  const [identities, setIdentities] = useState<Identity[]>([]);
  const [organization, setOrganization] = useState<Organization>({ organization: null, accounts: [] });
  const [billing, setBilling] = useState<Billing | null>(null);
  const [accountName, setAccountName] = useState(user.account_name);
  const [iamName, setIamName] = useState('');
  const [policy, setPolicy] = useState(POLICIES[1]);
  const [orgName, setOrgName] = useState(user.account_name);
  const [memberName, setMemberName] = useState('');
  const [memberEmail, setMemberEmail] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<{ kind: 'iam' | 'member'; id: string; name: string } | null>(null);

  const load = useCallback(async () => {
    try {
      if (section === 'iam') setIdentities((await api<{ identities: Identity[] }>('/mock/iam')).identities);
      if (section === 'organizations') { const result = await api<Organization>('/mock/organizations'); setOrganization(result); setOrgName(result.organization?.name || user.account_name); }
      if (section === 'billing') setBilling(await api<Billing>('/mock/billing'));
    } catch (err) { if (err instanceof ApiError && err.status === 401) onExpired(); else setError(err instanceof Error ? err.message : 'Unable to load account data'); }
  }, [section, user.account_name]);
  useEffect(() => { setError(''); setMessage(''); load(); }, [load]);

  async function action(work: () => Promise<void>, success: string) {
    setBusy(true); setError(''); setMessage('');
    try { await work(); await load(); setMessage(success); }
    catch (err) { if (err instanceof ApiError && err.status === 401) onExpired(); else setError(err instanceof Error ? err.message : 'Unable to save changes'); }
    finally { setBusy(false); }
  }
  const status = <>{error && <div className="form-error" role="alert">{error}</div>}{message && <div className="account-success" role="status">✓ {message}</div>}</>;

  if (section === 'account') return <><h1>Account settings</h1>{status}<section className="panel form-panel"><h2>Account details</h2><dl className="account-dl"><dt>Account ID</dt><dd className="mono">{user.account_id}</dd><dt>Account type</dt><dd>{user.account_type === 'personal' ? 'Personal' : 'Organization'}</dd><dt>Username</dt><dd>{user.username}</dd><dt>Email</dt><dd>{user.email}</dd><dt>Role</dt><dd>{user.role}</dd><dt>Created</dt><dd>{new Date(user.created_at).toLocaleDateString()}</dd></dl><form onSubmit={e => { e.preventDefault(); action(async () => { onUserUpdate(await api<User>('/account', { method: 'PATCH', body: JSON.stringify({ name: accountName }) })); }, 'Account name updated'); }}><div className="field"><label>Account name</label><input className="wide-input" value={accountName} onChange={e => setAccountName(e.target.value)} maxLength={100} required /></div><button className="btn primary" disabled={busy}>Save changes</button></form></section><section className="panel form-panel"><h2>Workspace access</h2><p>You own this workspace. Hosted zones, records, and account profiles are visible only when signed in to this account.</p>{user.is_demo && <p>This is the shared local demo account. Create your own account for a private workspace.</p>}</section></>;

  if (section === 'iam') return <><div className="zone-heading"><h1>Identity and Access Management</h1><span className="badge">Mock service</span></div>{status}<section className="panel form-panel"><h2>Signed-in identity</h2><dl className="account-dl"><dt>User</dt><dd>{user.username}</dd><dt>Role</dt><dd>{user.role}</dd><dt>ARN</dt><dd className="mono">arn:aws:iam::{user.account_id}:root</dd></dl><p>Mock IAM identities let you explore user and policy management. They do not have sign-in credentials or access to another user’s workspace.</p></section><section className="panel form-panel"><h2>Create mock IAM identity</h2><form onSubmit={e => { e.preventDefault(); action(async () => { await api('/mock/iam/users', { method: 'POST', body: JSON.stringify({ name: iamName, policy }) }); setIamName(''); }, 'Mock IAM identity created'); }}><div className="two-col"><div className="field"><label>User name</label><input value={iamName} onChange={e => setIamName(e.target.value)} pattern="[a-zA-Z0-9._-]+" minLength={3} maxLength={64} required /></div><div className="field"><label>Mock policy</label><select value={policy} onChange={e => setPolicy(e.target.value)}>{POLICIES.map(p => <option key={p}>{p}</option>)}</select></div></div><button className="btn primary" disabled={busy}>Create identity</button></form></section><section className="panel table-panel"><div className="panel-head"><h2>Mock users ({identities.length})</h2></div><div className="table-scroll"><table><thead><tr><th>Name</th><th>Policy</th><th>Actions</th></tr></thead><tbody>{identities.map(item => <tr key={item.id}><td>{item.name}</td><td><select aria-label={`Policy for ${item.name}`} value={item.policy} disabled={busy} onChange={e => { const policy = e.target.value; action(async () => { await api(`/mock/iam/users/${item.id}`, { method: 'PUT', body: JSON.stringify({ name: item.name, policy }) }); }, 'Mock policy updated'); }}>{POLICIES.map(p => <option key={p}>{p}</option>)}</select></td><td><button className="btn" onClick={() => setPendingDelete({ kind: 'iam', id: item.id, name: item.name })}>Delete</button></td></tr>)}</tbody></table>{!identities.length && <div className="empty">No mock IAM users. Create one above.</div>}</div></section>{deleteModal()}</>;

  if (section === 'organizations') return <><div className="zone-heading"><h1>Organizations</h1><span className="badge">Mock service</span></div>{status}<section className="panel form-panel"><h2>{organization.organization ? 'Organization details' : 'Create an organization'}</h2><p>Organize simulated member accounts. These members do not create real AWS accounts, app logins, or shared DNS access.</p>{organization.organization && <p>Organization ID: <span className="mono">{organization.organization.id}</span></p>}<form onSubmit={e => { e.preventDefault(); action(async () => { await api('/mock/organizations', { method: organization.organization ? 'PATCH' : 'POST', body: JSON.stringify({ name: orgName }) }); }, organization.organization ? 'Organization updated' : 'Mock organization created'); }}><div className="field"><label>Organization name</label><input className="wide-input" value={orgName} onChange={e => setOrgName(e.target.value)} maxLength={100} required /></div><button className="btn primary" disabled={busy}>{organization.organization ? 'Save changes' : 'Create organization'}</button></form></section>{organization.organization && <><section className="panel form-panel"><h2>Add mock member account</h2><form onSubmit={e => { e.preventDefault(); action(async () => { await api('/mock/organizations/accounts', { method: 'POST', body: JSON.stringify({ name: memberName, email: memberEmail }) }); setMemberName(''); setMemberEmail(''); }, 'Mock member account added'); }}><div className="two-col"><div className="field"><label>Account name</label><input value={memberName} onChange={e => setMemberName(e.target.value)} maxLength={100} required /></div><div className="field"><label>Email</label><input type="email" value={memberEmail} onChange={e => setMemberEmail(e.target.value)} maxLength={254} required /></div></div><button className="btn primary" disabled={busy}>Add member</button></form></section><section className="panel table-panel"><div className="panel-head"><h2>Member accounts ({organization.accounts.length})</h2></div><div className="table-scroll"><table><thead><tr><th>Account name</th><th>Mock account ID</th><th>Email</th><th>Actions</th></tr></thead><tbody>{organization.accounts.map(item => <tr key={item.id}><td>{item.name}</td><td className="mono">{item.id}</td><td>{item.email}</td><td><button className="btn" onClick={() => setPendingDelete({ kind: 'member', id: item.id, name: item.name })}>Remove</button></td></tr>)}</tbody></table>{!organization.accounts.length && <div className="empty">No mock member accounts.</div>}</div></section></>}{deleteModal()}</>;

  if (section === 'billing') return <><div className="zone-heading"><h1>Billing and cost management</h1><span className="badge">Mock service</span></div>{status}<section className="panel form-panel"><h2>Simulated usage — {billing?.period || 'Loading…'}</h2><p>This page shows illustrative usage for your account. No payment is collected and no real AWS bill is generated.</p>{billing && <><div className="billing-grid"><div><small>Hosted zones</small><strong>{billing.hosted_zones}</strong></div><div><small>Record sets</small><strong>{billing.record_sets}</strong></div><div><small>DNS queries</small><strong>{billing.dns_queries}</strong></div><div><small>Illustrative monthly total</small><strong>${billing.illustrative_total.toFixed(2)}</strong></div></div><p>{billing.payment_status}</p><p className="field-help">The example total uses $0.50 per zone. It is simulated pricing for this app.</p></>}</section></>;
  return null;

  function deleteModal() {
    if (!pendingDelete) return null;
    const selected = pendingDelete;
    return <div className="modal-scrim"><div className="modal"><div className="modal-title"><h2>Remove {selected.name}?</h2><button className="close" onClick={() => setPendingDelete(null)}>×</button></div><p>This removes the simulated entry from your account.</p><div className="modal-actions"><button className="btn" onClick={() => setPendingDelete(null)}>Cancel</button><button className="btn danger" disabled={busy} onClick={() => action(async () => { await api(selected.kind === 'iam' ? `/mock/iam/users/${selected.id}` : `/mock/organizations/accounts/${selected.id}`, { method: 'DELETE' }); setPendingDelete(null); }, 'Mock entry removed')}>Remove</button></div></div></div>;
  }
}
