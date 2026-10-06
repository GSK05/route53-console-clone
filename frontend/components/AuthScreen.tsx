'use client';

import { useState } from 'react';
import { api, User } from './types';

export default function AuthScreen({ onAuthenticated }: { onAuthenticated: (user: User) => void }) {
  const [signup, setSignup] = useState(false);
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [accountName, setAccountName] = useState('');
  const [accountType, setAccountType] = useState<'personal' | 'organization'>('personal');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault(); setError('');
    if (signup && password !== confirm) { setError('Passwords do not match.'); return; }
    setBusy(true);
    try {
      const body = signup ? { username, email, password, account_name: accountName, account_type: accountType } : { username, password };
      const user = await api<User>(signup ? '/auth/register' : '/auth/login', { method: 'POST', body: JSON.stringify(body) });
      onAuthenticated(user);
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to sign in'); }
    finally { setBusy(false); }
  }

  return <main className="login-shell"><div className={`login-card ${signup ? 'signup-card' : ''}`}>
    <div className="login-brand"><span className="aws-word">aws</span><span className="login-divider" />Route 53</div>
    <h1>{signup ? 'Create your console account' : 'Sign in to the console'}</h1>
    <p>{signup ? 'Your account gets a private workspace for hosted zones and records.' : 'Use the credentials you created for this application.'}</p>
    {error && <div className="form-error" role="alert">{error}</div>}
    <form onSubmit={submit}>
      {signup && <><fieldset className="account-type-field"><legend>Account type</legend><div className="zone-tiles"><button type="button" className={`tile ${accountType === 'personal' ? 'chosen' : ''}`} onClick={() => setAccountType('personal')}><span className="radio">{accountType === 'personal' && '●'}</span><span><strong>Personal</strong><small>Manage your own private workspace.</small></span></button><button type="button" className={`tile ${accountType === 'organization' ? 'chosen' : ''}`} onClick={() => setAccountType('organization')}><span className="radio">{accountType === 'organization' && '●'}</span><span><strong>Organization</strong><small>Includes a mock organization profile.</small></span></button></div></fieldset><label>{accountType === 'organization' ? 'Organization / account name' : 'Account name'}<input value={accountName} onChange={e => setAccountName(e.target.value)} maxLength={100} autoComplete="organization" required /></label><label>Email<input type="email" value={email} onChange={e => setEmail(e.target.value)} maxLength={254} autoComplete="email" required /></label></>}
      <label>{signup ? 'Username' : 'Username or email'}<input value={username} onChange={e => setUsername(e.target.value)} minLength={signup ? 3 : 1} maxLength={signup ? 64 : 254} pattern={signup ? '[a-zA-Z0-9._-]+' : undefined} autoComplete="username" required />{signup && <small className="field-help">Use letters, numbers, periods, underscores, or hyphens.</small>}</label>
      <label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} minLength={signup ? 12 : 1} maxLength={128} autoComplete={signup ? 'new-password' : 'current-password'} required />{signup && <small className="field-help">Use at least 12 characters.</small>}</label>
      {signup && <label>Confirm password<input type="password" value={confirm} onChange={e => setConfirm(e.target.value)} maxLength={128} autoComplete="new-password" required /></label>}
      <button className="btn primary full" disabled={busy}>{busy ? 'Please wait…' : signup ? 'Create account' : 'Sign in'}</button>
    </form>
    <p className="auth-switch">{signup ? 'Already have an account?' : 'First visit?'} <button className="table-link" onClick={() => { setSignup(!signup); setError(''); setPassword(''); setConfirm(''); }}>{signup ? 'Sign in' : 'Create an account'}</button></p>
    <div className="demo-hint">AWS services are simulated. These credentials belong to this application.</div>
  </div></main>;
}
