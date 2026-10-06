'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { api, PageResult, RecordSet, Zone } from './types';

const TYPES = ['A', 'AAAA', 'CNAME', 'TXT', 'MX', 'NS', 'PTR', 'SRV', 'CAA'];
const TYPE_LABELS: Record<string, string> = {
  A: 'A – Routes traffic to an IPv4 address and some AWS resources',
  AAAA: 'AAAA – Routes traffic to an IPv6 address',
  CNAME: 'CNAME – Routes traffic to another domain name',
  TXT: 'TXT – Text record', MX: 'MX – Mail exchange', NS: 'NS – Name server',
  PTR: 'PTR – Pointer', SRV: 'SRV – Service locator', CAA: 'CAA – Certification authority authorization',
};
const EMPTY_RECORD = { name: '', type: 'A', ttl: 300, values: '' };
type RecordForm = typeof EMPTY_RECORD;
type Toast = { text: string; kind: 'success' | 'error' };

export default function Console() {
  const router = useRouter();
  const pathname = usePathname();
  const segments = pathname.split('/').filter(Boolean);
  const zoneId = segments[0] === 'zones' && segments[1] && segments[1] !== 'new' ? segments[1] : null;
  const isNewZone = segments[0] === 'zones' && segments[1] === 'new';
  const section = segments[0] || 'dashboard';
  const [user, setUser] = useState<string | null | undefined>(undefined);
  const [loginName, setLoginName] = useState('demo');
  const [loginPassword, setLoginPassword] = useState('route53demo');
  const [theme, setTheme] = useState<'light' | 'dark'>('light');
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [toast, setToast] = useState<Toast | null>(null);
  const [zone, setZone] = useState<Zone | null>(null);
  const [zones, setZones] = useState<PageResult<Zone>>({ items: [], total: 0, page: 1, page_size: 10 });
  const [records, setRecords] = useState<PageResult<RecordSet>>({ items: [], total: 0, page: 1, page_size: 10 });
  const [query, setQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [selectedZone, setSelectedZone] = useState<string | null>(null);
  const [selectedRecords, setSelectedRecords] = useState<string[]>([]);
  const [zoneName, setZoneName] = useState('');
  const [zoneComment, setZoneComment] = useState('');
  const [zoneType, setZoneType] = useState<'public' | 'private'>('public');
  const [vpcRegion, setVpcRegion] = useState('us-east-1');
  const [vpcId, setVpcId] = useState('');
  const [tags, setTags] = useState<{ key: string; value: string }[]>([]);
  const [showZoneEdit, setShowZoneEdit] = useState(false);
  const [recordMode, setRecordMode] = useState<'create' | 'edit' | null>(null);
  const [editingRecord, setEditingRecord] = useState<RecordSet | null>(null);
  const [recordForms, setRecordForms] = useState<RecordForm[]>([{ ...EMPTY_RECORD }]);
  const [showDelete, setShowDelete] = useState<'zone' | 'records' | null>(null);
  const [activeTab, setActiveTab] = useState('records');
  const [busy, setBusy] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const uploadRef = useRef<HTMLInputElement>(null);

  const flash = (text: string, kind: Toast['kind'] = 'success') => setToast({ text, kind });
  const fail = (err: unknown) => flash(err instanceof Error ? err.message : 'Something went wrong', 'error');
  const navigate = (path: string) => { setQuery(''); setTypeFilter('all'); setPage(1); setSelectedRecords([]); setSelectedZone(null); router.push(path); };

  const loadZones = useCallback(async () => {
    const params = new URLSearchParams({ q: query, type: typeFilter, page: String(page), page_size: '10' });
    try { setZones(await api<PageResult<Zone>>(`/zones?${params}`)); } catch (err) { fail(err); }
  }, [query, typeFilter, page]);
  const loadZone = useCallback(async () => {
    if (!zoneId) return;
    try { setZone(await api<Zone>(`/zones/${zoneId}`)); } catch (err) { fail(err); }
  }, [zoneId]);
  const loadRecords = useCallback(async () => {
    if (!zoneId) return;
    const params = new URLSearchParams({ q: query, type: typeFilter, page: String(page), page_size: '10' });
    try { setRecords(await api<PageResult<RecordSet>>(`/zones/${zoneId}/records?${params}`)); } catch (err) { fail(err); }
  }, [zoneId, query, typeFilter, page]);

  useEffect(() => { api<{ username: string }>('/auth/me').then(x => setUser(x.username)).catch(() => setUser(null)); }, []);
  useEffect(() => { const saved = localStorage.getItem('route53-theme'); if (saved === 'dark') setTheme('dark'); }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('route53-theme', theme); }, [theme]);
  useEffect(() => { if (user && section === 'zones' && !zoneId && !isNewZone) loadZones(); }, [user, section, zoneId, isNewZone, loadZones]);
  useEffect(() => { if (user && zoneId) { loadZone(); loadRecords(); } }, [user, zoneId, loadZone, loadRecords]);
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 4500); return () => clearTimeout(t); }, [toast]);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const input = event.target instanceof HTMLElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName);
      if (event.key === 'Escape') { setRecordMode(null); setShowZoneEdit(false); setShowDelete(null); setShowShortcuts(false); }
      if (input || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === '/') { event.preventDefault(); searchRef.current?.focus(); }
      if (event.key.toLowerCase() === 'n' && section === 'zones') { event.preventDefault(); zoneId ? openCreateRecord() : navigate('/zones/new'); }
      if (event.key === '?') setShowShortcuts(true);
    };
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler);
  });

  async function handleLogin(event: React.FormEvent) {
    event.preventDefault(); setBusy(true);
    try { const result = await api<{ username: string }>('/auth/login', { method: 'POST', body: JSON.stringify({ username: loginName, password: loginPassword }) }); setUser(result.username); flash('Signed in successfully'); }
    catch (err) { fail(err); } finally { setBusy(false); }
  }
  async function logout() { await api('/auth/logout', { method: 'POST' }); setUser(null); navigate('/'); }
  async function createZone(event: React.FormEvent) {
    event.preventDefault(); setBusy(true);
    try {
      const result = await api<Zone>('/zones', { method: 'POST', body: JSON.stringify({ name: zoneName, type: zoneType, comment: zoneComment, vpc_region: zoneType === 'private' ? vpcRegion : null, vpc_id: zoneType === 'private' ? vpcId : null, tags: tags.filter(t => t.key.trim()) }) });
      flash('Hosted zone created'); navigate(`/zones/${result.id}`);
    } catch (err) { fail(err); } finally { setBusy(false); }
  }
  async function saveComment(event: React.FormEvent) {
    event.preventDefault(); if (!zone) return; setBusy(true);
    try { await api(`/zones/${zone.id}`, { method: 'PATCH', body: JSON.stringify({ comment: zoneComment }) }); setShowZoneEdit(false); if (zoneId) await loadZone(); else await loadZones(); flash('Hosted zone updated'); }
    catch (err) { fail(err); } finally { setBusy(false); }
  }
  async function deleteZone() {
    if (!zoneId && !selectedZone) return; setBusy(true);
    try { await api(`/zones/${zoneId || selectedZone}`, { method: 'DELETE' }); setShowDelete(null); flash('Hosted zone deleted'); navigate('/zones'); loadZones(); }
    catch (err) { fail(err); } finally { setBusy(false); }
  }
  function openCreateRecord() { setEditingRecord(null); setRecordForms([{ ...EMPTY_RECORD }]); setRecordMode('create'); }
  function openEditRecord(record: RecordSet) {
    if (record.is_default) return;
    setEditingRecord(record); setRecordForms([{ name: record.name === zone?.name ? '' : record.name.slice(0, -(zone!.name.length + 1)), type: record.type, ttl: record.ttl, values: record.values.join('\n') }]); setRecordMode('edit');
  }
  function updateRecordForm(index: number, patch: Partial<RecordForm>) { setRecordForms(old => old.map((r, i) => i === index ? { ...r, ...patch } : r)); }
  async function saveRecords(event: React.FormEvent) {
    event.preventDefault(); if (!zoneId) return; setBusy(true);
    try {
      for (const form of recordForms) {
        const body = JSON.stringify({ name: form.name, type: form.type, ttl: Number(form.ttl), values: form.values.split('\n').map(s => s.trim()).filter(Boolean) });
        await api(`/zones/${zoneId}/records${recordMode === 'edit' ? `/${editingRecord?.id}` : ''}`, { method: recordMode === 'edit' ? 'PUT' : 'POST', body });
      }
      setRecordMode(null); setEditingRecord(null); await Promise.all([loadZone(), loadRecords()]); flash(recordMode === 'edit' ? 'Record updated' : `${recordForms.length} record${recordForms.length > 1 ? 's' : ''} created`);
    } catch (err) { fail(err); } finally { setBusy(false); }
  }
  async function deleteRecords() {
    if (!zoneId || !selectedRecords.length) return; setBusy(true);
    try { await api(`/zones/${zoneId}/records/bulk-delete`, { method: 'POST', body: JSON.stringify({ ids: selectedRecords }) }); setSelectedRecords([]); setShowDelete(null); await Promise.all([loadZone(), loadRecords()]); flash('Records deleted'); }
    catch (err) { fail(err); } finally { setBusy(false); }
  }
  async function importZone(file: File) {
    if (!zoneId) return; setBusy(true);
    try { const content = await file.text(); const result = await api<{ imported: number }>(`/zones/${zoneId}/import`, { method: 'POST', body: JSON.stringify({ content }) }); await Promise.all([loadZone(), loadRecords()]); flash(`${result.imported} record sets imported`); }
    catch (err) { fail(err); } finally { setBusy(false); if (uploadRef.current) uploadRef.current.value = ''; }
  }
  function toggleRecord(id: string) { setSelectedRecords(old => old.includes(id) ? old.filter(x => x !== id) : [...old, id]); }
  const pageCount = Math.max(1, Math.ceil((zoneId ? records.total : zones.total) / 10));

  if (user === undefined) return <div className="boot-screen"><div className="spinner" />Loading console…</div>;
  if (!user) return <main className="login-shell"><div className="login-card"><div className="login-brand"><span className="aws-word">aws</span><span className="login-divider" />Route 53</div><h1>Sign in to the console</h1><p>This is a local demo of the Route 53 experience.</p><form onSubmit={handleLogin}><label>Username<input value={loginName} onChange={e => setLoginName(e.target.value)} required /></label><label>Password<input type="password" value={loginPassword} onChange={e => setLoginPassword(e.target.value)} required /></label><button className="btn primary full" disabled={busy}>Sign in</button></form><div className="demo-hint">Demo credentials: <strong>demo / route53demo</strong></div></div>{toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}</main>;

  return <div className="app-shell">
    <header className="topbar"><button className="top-icon" onClick={() => setSidebarOpen(!sidebarOpen)} aria-label="Toggle navigation">☰</button><button className="aws-logo" onClick={() => navigate('/')}>aws<span className="smile">⌣</span></button><span className="top-separator" /><button className="services" onClick={() => navigate('/')}>▦ <span>Services</span></button><div className="global-search">⌕ <span>Search</span></div><div className="top-spacer" /><button className="top-icon" title="Keyboard shortcuts" onClick={() => setShowShortcuts(true)}>⌨</button><button className="top-icon" title="Toggle dark mode" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme === 'light' ? '◐' : '☀'}</button><button className="top-icon" title="Help" onClick={() => setShowShortcuts(true)}>ⓘ</button><span className="top-separator" /><button className="account" onClick={logout}>{user} ▾<small>Sign out</small></button></header>
    <div className="body-shell">
      {sidebarOpen && <aside className="sidebar"><div className="side-title"><strong>Route 53</strong><button onClick={() => setSidebarOpen(false)}>×</button></div><nav><button className={section === 'dashboard' ? 'active' : ''} onClick={() => navigate('/')}>Dashboard</button><button className={section === 'zones' ? 'active' : ''} onClick={() => navigate('/zones')}>Hosted zones</button><button onClick={() => navigate('/health-checks')}>Health checks</button><button onClick={() => navigate('/profiles')}>Profiles <span className="new-label">New</span></button><h4>▾ &nbsp; IP-based routing</h4><button onClick={() => navigate('/cidr-collections')}>CIDR collections</button><h4>▾ &nbsp; Traffic flow</h4><button onClick={() => navigate('/traffic-policies')}>Traffic policies</button><button onClick={() => navigate('/policy-records')}>Policy records</button><h4>▾ &nbsp; Domains</h4><button onClick={() => navigate('/registered-domains')}>Registered domains</button><button onClick={() => navigate('/requests')}>Requests</button><h4>▾ &nbsp; Resolver</h4><button onClick={() => navigate('/resolver')}>VPCs</button><button onClick={() => navigate('/inbound-endpoints')}>Inbound endpoints</button><button onClick={() => navigate('/outbound-endpoints')}>Outbound endpoints</button><button onClick={() => navigate('/rules')}>Rules</button></nav></aside>}
      <main className="content">
        <div className="breadcrumbs"><button onClick={() => navigate('/')}>Route 53</button>{section !== 'dashboard' && <> › <button onClick={() => navigate('/zones')}>{section === 'zones' ? 'Hosted zones' : section.replaceAll('-', ' ')}</button></>}{isNewZone && <> › <span>Create hosted zone</span></>}{zoneId && zone && <> › <span>{zone.name}</span></>}</div>
        {section === 'dashboard' && <><h1>Route 53 dashboard</h1><div className="dashboard-grid"><div className="panel"><h2>DNS management</h2><p>Manage hosted zones and DNS records from one place.</p><button className="btn primary" onClick={() => navigate('/zones')}>View hosted zones</button></div><div className="panel"><h2>Get started</h2><p>Create a hosted zone to organize DNS records for your domain.</p><button className="btn" onClick={() => navigate('/zones/new')}>Create hosted zone</button></div></div></>}
        {isNewZone && <><h1>Create hosted zone <span className="info">Info</span></h1><form onSubmit={createZone}><section className="panel form-panel"><h2>Hosted zone configuration</h2><p>A hosted zone is a container that holds information about how you want to route traffic for a domain, such as example.com, and its subdomains.</p><div className="field"><label>Domain name <span className="info">Info</span></label><div className="field-help">This is the name of the domain that you want to route traffic for.</div><input className="wide-input" placeholder="example.com" value={zoneName} onChange={e => setZoneName(e.target.value)} required /></div><div className="field"><label>Description <em>– optional</em> <span className="info">Info</span></label><div className="field-help">This value lets you distinguish hosted zones that have the same name.</div><textarea className="wide-input" placeholder="The hosted zone is used for..." maxLength={256} value={zoneComment} onChange={e => setZoneComment(e.target.value)} /><div className="field-help">The description can have up to 256 characters. {zoneComment.length}/256</div></div><div className="field"><label>Type <span className="info">Info</span></label><div className="field-help">The type indicates whether you want to route traffic on the internet or in an Amazon VPC.</div><div className="zone-tiles"><button type="button" className={`tile ${zoneType === 'public' ? 'chosen' : ''}`} onClick={() => setZoneType('public')}><span className="radio">{zoneType === 'public' && '●'}</span><span><strong>Public hosted zone</strong><small>A public hosted zone determines how traffic is routed on the internet.</small></span></button><button type="button" className={`tile ${zoneType === 'private' ? 'chosen' : ''}`} onClick={() => setZoneType('private')}><span className="radio">{zoneType === 'private' && '●'}</span><span><strong>Private hosted zone</strong><small>A private hosted zone determines how traffic is routed within an Amazon VPC.</small></span></button></div></div>{zoneType === 'private' && <div className="field"><label>VPC to associate with the hosted zone</label><div className="two-col"><div><small>Region</small><select value={vpcRegion} onChange={e => setVpcRegion(e.target.value)}><option value="us-east-1">US East (N. Virginia)</option><option value="us-west-2">US West (Oregon)</option><option value="ap-south-1">Asia Pacific (Mumbai)</option></select></div><div><small>VPC ID</small><input placeholder="vpc-0123456789abcdef" value={vpcId} onChange={e => setVpcId(e.target.value)} required /></div></div></div>}</section><section className="panel form-panel"><h2>Tags <span className="info">Info</span></h2><p>Apply tags to hosted zones to help organize and identify them.</p>{tags.length === 0 && <p className="muted">No tags associated with the resource.</p>}{tags.map((tag, i) => <div className="tag-row" key={i}><input aria-label="Tag key" placeholder="Key" value={tag.key} onChange={e => setTags(tags.map((t, j) => j === i ? { ...t, key: e.target.value } : t))} /><input aria-label="Tag value" placeholder="Value" value={tag.value} onChange={e => setTags(tags.map((t, j) => j === i ? { ...t, value: e.target.value } : t))} /><button type="button" className="btn" onClick={() => setTags(tags.filter((_, j) => j !== i))}>Remove</button></div>)}<button type="button" className="btn outline-blue" onClick={() => setTags([...tags, { key: '', value: '' }])}>Add tag</button><div className="field-help">You can add up to 50 tags.</div></section><div className="form-actions"><button type="button" className="btn text" onClick={() => navigate('/zones')}>Cancel</button><button className="btn primary" disabled={busy}>Create hosted zone</button></div></form></>}
        {section === 'zones' && !isNewZone && !zoneId && <><h1>Hosted zones</h1><section className="panel table-panel"><div className="panel-head"><div><h2>Hosted zones <span className="count">({zones.total})</span></h2><div className="search-note">Automatic mode is the current search behavior optimized for best filter results. <a href="#" onClick={e => e.preventDefault()}>To change modes go to settings.</a></div></div><div className="toolbar"><button className="btn icon-btn" title="Refresh" onClick={loadZones}>↻</button><button className="btn" disabled={!selectedZone} onClick={() => selectedZone && navigate(`/zones/${selectedZone}`)}>View details</button><button className="btn" disabled={!selectedZone} onClick={() => { const chosen = zones.items.find(z => z.id === selectedZone); if (chosen) { setZone(chosen); setZoneComment(chosen.comment); setShowZoneEdit(true); } }}>Edit</button><button className="btn" disabled={!selectedZone} onClick={() => setShowDelete('zone')}>Delete</button><button className="btn primary" onClick={() => navigate('/zones/new')}>Create hosted zone</button></div></div><div className="filter-row"><div className="search-wrap">⌕ <input ref={searchRef} placeholder="Filter records by property or value" value={query} onChange={e => { setQuery(e.target.value); setPage(1); }} /></div><select aria-label="Zone type filter" value={typeFilter} onChange={e => { setTypeFilter(e.target.value); setPage(1); }}><option value="all">All types</option><option value="public">Public</option><option value="private">Private</option></select><Pagination page={page} pages={pageCount} setPage={setPage} /></div><div className="table-scroll"><table><thead><tr><th className="checkbox-col"></th><th>Hosted zone name <span>⌄</span></th><th>Type <span>⌄</span></th><th>Created by <span>⌄</span></th><th>Record count <span>⌄</span></th><th>Description <span>⌄</span></th><th>Hosted zone ID <span>⌄</span></th></tr></thead><tbody>{zones.items.map(z => <tr key={z.id} className={selectedZone === z.id ? 'selected' : ''}><td><input type="radio" name="zone-select" aria-label={`Select ${z.name}`} checked={selectedZone === z.id} onChange={() => setSelectedZone(z.id)} /></td><td><button className="table-link" onClick={() => navigate(`/zones/${z.id}`)}>{z.name}</button></td><td>{z.type === 'public' ? 'Public' : 'Private'}</td><td>Route 53</td><td>{z.record_count}</td><td title={z.comment}>{z.comment || '–'}</td><td className="mono">{z.id}</td></tr>)}</tbody></table>{!zones.items.length && <div className="empty">No hosted zones found. {query ? 'Try another search.' : 'Create a hosted zone to get started.'}</div>}</div></section></>}
        {zoneId && zone && <><div className="zone-heading"><div><span className="badge">{zone.type === 'public' ? 'Public' : 'Private'}</span><h1>{zone.name}</h1><span className="info">Info</span></div><div className="toolbar"><button className="btn" onClick={() => setShowDelete('zone')}>Delete zone</button><button className="btn" onClick={() => flash('This demo does not resolve DNS queries.', 'error')}>Test record</button><button className="btn" onClick={() => flash('Query logging is outside this demo scope.', 'error')}>Configure query logging</button></div></div><section className="panel details-panel"><div className="details-head"><h2>▾ &nbsp;Hosted zone details</h2><button className="btn" onClick={() => { setZoneComment(zone.comment); setShowZoneEdit(true); }}>Edit hosted zone</button></div><div className="details-grid"><div><dt>Hosted zone name</dt><dd>{zone.name}</dd><dt>Hosted zone ID</dt><dd className="mono">{zone.id}</dd><dt>Description</dt><dd>{zone.comment || '–'}</dd></div><div><dt>Query log</dt><dd>–</dd><dt>Type</dt><dd>{zone.type === 'public' ? 'Public hosted zone' : 'Private hosted zone'}</dd><dt>Record count</dt><dd>{zone.record_count}</dd></div><div><dt>{zone.type === 'public' ? 'Name servers' : 'VPC'}</dt><dd>{zone.type === 'public' ? zone.name_servers.map(s => <div key={s}>{s}</div>) : `${zone.vpc_id} (${zone.vpc_region})`}</dd></div></div></section><div className="tabs"><button className={activeTab === 'records' ? 'active' : ''} onClick={() => setActiveTab('records')}>Records ({zone.record_count})</button><button className={activeTab === 'dnssec' ? 'active' : ''} onClick={() => setActiveTab('dnssec')}>DNSSEC signing</button><button className={activeTab === 'tags' ? 'active' : ''} onClick={() => setActiveTab('tags')}>Hosted zone tags ({zone.tags.length})</button></div>{activeTab === 'records' ? <section className="panel table-panel"><div className="panel-head"><div><h2>Records <span className="count">({records.total})</span> <span className="info">Info</span></h2><div className="search-note">Automatic mode is the current search behavior optimized for best filter results. <a href="#" onClick={e => e.preventDefault()}>To change modes go to settings.</a></div></div><div className="toolbar"><button className="btn icon-btn" title="Refresh" onClick={() => { loadZone(); loadRecords(); }}>↻</button><button className="btn" disabled={!selectedRecords.length} onClick={() => setShowDelete('records')}>Delete record{selectedRecords.length > 1 ? 's' : ''}</button><input ref={uploadRef} className="hidden" type="file" accept=".zone,.txt,.bind,text/plain" onChange={e => e.target.files?.[0] && importZone(e.target.files[0])} /><button className="btn" disabled={busy} onClick={() => uploadRef.current?.click()}>Import zone file</button><button className="btn" onClick={() => window.location.assign(`/api/zones/${zoneId}/export?format=json`)}>Export JSON</button><button className="btn" onClick={() => window.location.assign(`/api/zones/${zoneId}/export?format=bind`)}>Export BIND</button><button className="btn primary" onClick={openCreateRecord}>Create record</button></div></div><div className="filter-row"><div className="search-wrap">⌕ <input ref={searchRef} placeholder="Filter records by property or value" value={query} onChange={e => { setQuery(e.target.value); setPage(1); }} /></div><select aria-label="Record type filter" value={typeFilter} onChange={e => { setTypeFilter(e.target.value); setPage(1); }}><option value="all">Type</option>{[...TYPES, 'SOA'].map(t => <option key={t}>{t}</option>)}</select><select aria-label="Routing policy filter" defaultValue="simple"><option value="simple">Simple routing</option></select><select aria-label="Alias filter" defaultValue="all"><option value="all">Alias</option><option value="no">No</option></select><Pagination page={page} pages={pageCount} setPage={setPage} /></div><div className="table-scroll"><table><thead><tr><th className="checkbox-col"><input type="checkbox" aria-label="Select all editable records" checked={records.items.filter(r => !r.is_default).length > 0 && records.items.filter(r => !r.is_default).every(r => selectedRecords.includes(r.id))} onChange={e => setSelectedRecords(e.target.checked ? records.items.filter(r => !r.is_default).map(r => r.id) : [])} /></th><th>Record name <span>⌄</span></th><th>Type <span>⌄</span></th><th>Routing policy <span>⌄</span></th><th>Differentiator <span>⌄</span></th><th>Alias <span>⌄</span></th><th>Value/Route traffic to <span>⌄</span></th><th>TTL (seconds) <span>⌄</span></th><th></th></tr></thead><tbody>{records.items.map(r => <tr key={r.id} className={selectedRecords.includes(r.id) ? 'selected' : ''}><td><input type="checkbox" aria-label={`Select ${r.name} ${r.type}`} disabled={r.is_default} checked={selectedRecords.includes(r.id)} onChange={() => toggleRecord(r.id)} /></td><td>{r.name}</td><td>{r.type}</td><td>{r.routing_policy}</td><td>–</td><td>No</td><td className="values-cell" title={r.values.join('\n')}>{r.values.map((v, i) => <div key={i}>{v}</div>)}</td><td>{r.ttl}</td><td>{!r.is_default && <button className="table-link" onClick={() => openEditRecord(r)}>Edit</button>}</td></tr>)}</tbody></table>{!records.items.length && <div className="empty">No records found.</div>}</div></section> : <section className="panel placeholder-panel"><h2>{activeTab === 'tags' ? 'Hosted zone tags' : 'DNSSEC signing'}</h2>{activeTab === 'tags' ? zone.tags.length ? zone.tags.map(t => <p key={t.key}><strong>{t.key}</strong> &nbsp; {t.value}</p>) : <p>No tags associated with this hosted zone.</p> : <p>Coming soon</p>}</section>}</>}
        {section !== 'dashboard' && section !== 'zones' && <section className="panel placeholder-panel"><h1>{section.split('-').map(s => s[0].toUpperCase() + s.slice(1)).join(' ')}</h1><p>Coming soon</p><button className="btn" onClick={() => navigate('/zones')}>Go to hosted zones</button></section>}
      </main>
    </div>
    {recordMode && zone && <div className={recordMode === 'edit' ? 'drawer-scrim' : 'page-scrim'}><div className={recordMode === 'edit' ? 'record-drawer' : 'record-dialog'}><div className="dialog-head"><h1>{recordMode === 'edit' ? 'Edit record' : 'Create record'} <span className="info">{recordMode === 'create' && 'Info'}</span></h1><button className="close" onClick={() => setRecordMode(null)}>×</button></div><form onSubmit={saveRecords}>{recordMode === 'create' && <div className="quick-head"><h2>Quick create record</h2><span className="info">Simple routing</span></div>}{recordForms.map((form, i) => <div className="record-form" key={i}>{recordMode === 'create' && <div className="record-form-head"><strong>▾ &nbsp;Record {i + 1}</strong>{recordForms.length > 1 && <button type="button" className="btn" onClick={() => setRecordForms(recordForms.filter((_, j) => j !== i))}>Delete</button>}</div>}<div className="two-col"><div className="field"><label>Record name <span className="info">Info</span></label><div className="suffix-input"><input value={form.name} onChange={e => updateRecordForm(i, { name: e.target.value })} /><span>.{zone.name}</span></div><div className="field-help">Keep blank to create a record for the root domain.</div></div><div className="field"><label>Record type <span className="info">Info</span></label><select value={form.type} onChange={e => updateRecordForm(i, { type: e.target.value })}>{TYPES.map(type => <option value={type} key={type}>{TYPE_LABELS[type]}</option>)}</select></div></div><div className="field alias-row"><span className="toggle-off" /> Alias <span className="field-help">AWS resource aliases are outside this demo scope.</span></div><div className="field"><label>Value <span className="info">Info</span></label><textarea className="values-input" value={form.values} onChange={e => updateRecordForm(i, { values: e.target.value })} placeholder={form.type === 'A' ? '192.0.2.1' : form.type === 'MX' ? '10 mail.example.com' : 'Enter record value'} required /><div className="field-help">Enter multiple values on separate lines.</div></div><div className="two-col"><div className="field"><label>TTL (seconds) <span className="info">Info</span></label><div className="ttl-row"><input type="number" min="0" value={form.ttl} onChange={e => updateRecordForm(i, { ttl: Number(e.target.value) })} required /><button type="button" className="btn" onClick={() => updateRecordForm(i, { ttl: 60 })}>1m</button><button type="button" className="btn" onClick={() => updateRecordForm(i, { ttl: 3600 })}>1h</button><button type="button" className="btn" onClick={() => updateRecordForm(i, { ttl: 86400 })}>1d</button></div><div className="field-help">Recommended values: 60 to 172800 (two days)</div></div><div className="field"><label>Routing policy <span className="info">Info</span></label><select value="simple" disabled><option value="simple">Simple routing</option></select></div></div></div>)}{recordMode === 'create' && <div className="add-record-row"><button type="button" className="btn" onClick={() => setRecordForms([...recordForms, { ...EMPTY_RECORD }])}>Add another record</button></div>}<div className="dialog-actions"><button type="button" className="btn text" onClick={() => setRecordMode(null)}>Cancel</button><button className="btn primary" disabled={busy}>{recordMode === 'edit' ? 'Save' : `Create record${recordForms.length > 1 ? 's' : ''}`}</button></div></form></div></div>}
    {showZoneEdit && zone && <div className="drawer-scrim"><div className="record-drawer"><div className="dialog-head"><h1>Edit hosted zone</h1><button className="close" onClick={() => setShowZoneEdit(false)}>×</button></div><form onSubmit={saveComment} className="edit-zone-form"><div className="field"><label>Hosted zone name</label><input value={zone.name} disabled /></div><div className="field"><label>Type</label><input value={`${zone.type[0].toUpperCase()}${zone.type.slice(1)} hosted zone`} disabled /></div><div className="field"><label>Description <span className="info">Info</span></label><textarea value={zoneComment} maxLength={256} onChange={e => setZoneComment(e.target.value)} /><div className="field-help">{zoneComment.length}/256 characters</div></div><div className="dialog-actions"><button type="button" className="btn text" onClick={() => setShowZoneEdit(false)}>Cancel</button><button className="btn primary" disabled={busy}>Save changes</button></div></form></div></div>}
    {showDelete && <div className="modal-scrim"><div className="modal"><div className="modal-title"><h2>Delete {showDelete === 'zone' ? 'hosted zone' : 'records'}</h2><button className="close" onClick={() => setShowDelete(null)}>×</button></div><p>{showDelete === 'zone' ? 'Delete this hosted zone? You must delete all non-default records first. This action cannot be undone.' : `Delete ${selectedRecords.length} selected record${selectedRecords.length === 1 ? '' : 's'}? This action cannot be undone.`}</p><div className="modal-actions"><button className="btn" onClick={() => setShowDelete(null)}>Cancel</button><button className="btn danger" disabled={busy} onClick={showDelete === 'zone' ? deleteZone : deleteRecords}>Delete</button></div></div></div>}
    {showShortcuts && <div className="modal-scrim"><div className="modal"><div className="modal-title"><h2>Keyboard shortcuts</h2><button className="close" onClick={() => setShowShortcuts(false)}>×</button></div><p><kbd>/</kbd> Focus search</p><p><kbd>N</kbd> Create a hosted zone or record</p><p><kbd>Esc</kbd> Close a dialog</p><p><kbd>?</kbd> Show shortcuts</p></div></div>}
    {toast && <div className={`toast ${toast.kind}`} role="status"><span>{toast.kind === 'success' ? '✓' : '!'}</span>{toast.text}<button onClick={() => setToast(null)}>×</button></div>}
  </div>;
}

function Pagination({ page, pages, setPage }: { page: number; pages: number; setPage: (value: number) => void }) {
  return <div className="pagination"><button aria-label="Previous page" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹</button><strong>{page}</strong><button aria-label="Next page" disabled={page >= pages} onClick={() => setPage(page + 1)}>›</button><span className="gear">⚙</span></div>;
}
