import { useCallback, useEffect, useState } from 'react';
import { ApiError, type ApiClient } from '../api';

interface CallRecord {
  id: string;
  provider: string;
  direction: string;
  from_number: string;
  to_number: string;
  status: string;
  summary: string | null;
  outcome: string | null;
  created_at: string;
}

interface AgentRecord { id: string; name: string; provider: string; voice: string; is_active: boolean }
interface DashboardData { counts: Record<string, number>; agents: AgentRecord[]; calls: CallRecord[] }

export const PlatformDashboard = ({ api, refreshVersion }: { api: ApiClient; refreshVersion: number }) => {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState('');
  const [simulating, setSimulating] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.get<DashboardData>('/api/dashboard'));
      setError('');
    } catch (requestError) {
      // A 401 tears the session down at the App level; showing a panel error
      // on top of the sign-in screen would just be noise.
      if (requestError instanceof ApiError && requestError.status === 401) return;
      setError('Operational dashboard data is unavailable.');
    }
  }, [api]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load, refreshVersion]);

  const simulate = async () => {
    const agent = data?.agents[0];
    if (!agent) return;
    setSimulating(true);
    setError('');
    try {
      await api.post('/api/telephony/simulate', {
        agent_id: agent.id,
        direction: 'outbound',
        from_number: '+911204000001',
        to_number: '+971500000001',
        transcript: 'Buyer requested a verified basmati rice supplier in Punjab and asked for a follow-up quote.',
        outcome: 'qualified_lead',
        idempotency_key: `dashboard-${crypto.randomUUID()}`,
      });
      await load();
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 401) return;
      setError(requestError instanceof Error ? requestError.message : 'Call simulation failed.');
    } finally {
      setSimulating(false);
    }
  };

  return (
    <section className="platform-dashboard" aria-labelledby="operations-heading">
      <div className="section-heading-row">
        <div><span className="eyebrow">Platform operations</span><h2 id="operations-heading">Voice control plane</h2><p>Persisted agents, call lifecycle, summaries, and outcomes.</p></div>
        <button className="btn btn-primary" onClick={() => void simulate()} disabled={simulating || !data?.agents.length}>{simulating ? 'Running…' : 'Simulate call'}</button>
      </div>
      {error && <div className="data-state error-state" role="alert">{error}</div>}
      <div className="metric-grid">
        {Object.entries(data?.counts ?? {}).map(([name, value]) => <div className="metric-card" key={name}><strong>{value}</strong><span>{name.replaceAll('_', ' ')}</span></div>)}
      </div>
      <div className="operations-grid">
        <div className="operations-panel"><h3>Voice agents</h3>{data?.agents.map((agent) => <article key={agent.id} className="compact-row"><div><strong>{agent.name}</strong><span>{agent.provider} · {agent.voice}</span></div><span className="status-pill">{agent.is_active ? 'active' : 'paused'}</span></article>)}</div>
        <div className="operations-panel"><h3>Recent calls</h3>{!data?.calls.length && <p className="empty-copy">No calls yet. Run the deterministic simulator.</p>}{data?.calls.slice(0, 6).map((call) => <article key={call.id} className="call-row"><div><strong>{call.direction} · {call.status}</strong><span>{call.provider} · {call.to_number}</span></div><p>{call.summary ?? 'Summary queued…'}</p><small>{call.outcome ?? 'No outcome tagged'}</small></article>)}</div>
      </div>
    </section>
  );
};
