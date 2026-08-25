import { useState, type FormEvent } from 'react';
import heroArtwork from '../assets/hero.png';

export interface AuthUser {
  id: string;
  workspace_id: string;
  workspace_name: string;
  email: string;
  name: string;
  role: string;
}

export interface AuthSession {
  accessToken: string;
  user: AuthUser;
}

export const Login = ({ onAuthenticated, notice = '' }: { onAuthenticated: (session: AuthSession) => void; notice?: string }) => {
  const [email, setEmail] = useState('arjun@horizon.example');
  const [password, setPassword] = useState('TradeVoice123!');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) throw new Error('Sign-in failed. Check the demo credentials and backend.');
      const body = await response.json() as { access_token: string; user: AuthUser };
      onAuthenticated({ accessToken: body.access_token, user: body.user });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Sign-in failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="login-shell">
      <section className="login-intro" aria-labelledby="login-intro-title">
        <div className="brand-lockup"><span className="brand-mark">TV</span><strong>TradeVoice</strong></div>
        <div>
          <span className="eyebrow">Voice operations, without the hand-waving</span>
          <h2 id="login-intro-title">A working control plane for safe, tenant-aware voice agents.</h2>
          <p>Follow a call from authenticated context to a confirmed business action, then watch the result persist across the dashboard.</p>
        </div>
        <ul className="login-proof-list">
          <li><strong>Scoped by default</strong><span>Every query and tool call stays inside its workspace.</span></li>
          <li><strong>Confirmation before writes</strong><span>Exact payloads are persisted, hashed, and consumed once.</span></li>
          <li><strong>Built to inspect</strong><span>Call state, summaries, request IDs, and tool traces stay visible.</span></li>
        </ul>
        <img className="login-artwork" src={heroArtwork} alt="" />
      </section>
      <section className="login-panel">
        <form className="login-card" onSubmit={submit}>
          <span className="eyebrow">Demo workspace</span>
          <h1>TradeVoice Operations</h1>
          <p>The fictional demo account is already filled in. Sign in and run the five-minute flow.</p>
          <label>Email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="username" required /></label>
          <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" minLength={8} required /></label>
          {notice && !error && <div className="data-state" role="status">{notice}</div>}
        {error && <div className="data-state error-state" role="alert">{error}</div>}
          <button className="btn btn-primary" type="submit" disabled={loading}>{loading ? 'Signing in…' : 'Open demo workspace'}</button>
          <small>No real customer data or paid provider credentials are used.</small>
        </form>
      </section>
    </main>
  );
};
