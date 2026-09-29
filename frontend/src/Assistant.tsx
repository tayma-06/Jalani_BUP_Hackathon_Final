import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from './api';
import type { Network } from './types';
import './assistant.css';

type Briefing = {
  source: 'ai' | 'template'; explanation: string; uncertainty: string; fallback_reason: string | null;
  tick: number | null; run_id: string; stale: boolean;
  sources: { id: string; title: string; data: unknown }[];
};

export default function Assistant({ state }: { state?: Network }) {
  const [task, setTask] = useState('network');
  const [record, setRecord] = useState('');
  const [result, setResult] = useState<Briefing | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const path = task === 'recommendation' ? '/recommendations' : '/incidents';
  const records = useQuery({ queryKey: [path], queryFn: () => api<{ id: string; station_id?: string; text?: string }[]>(path), enabled: task !== 'network', refetchInterval: 5000 });
  async function explain() {
    setBusy(true); setError(''); setResult(null);
    try { setResult(await api<Briefing>('/assistant', 'POST', { task, record_id: record || null })); }
    catch (e) { setError(e instanceof Error ? e.message : 'Unable to load briefing.'); }
    finally { setBusy(false); }
  }
  const outdated = result && (result.stale || result.run_id !== state?.run_id || result.tick !== state?.tick);
  return <section className="panel assistant-panel">
    <h2>Operator assistant</h2>
    <p>Explain recommendations and incidents using recorded evidence. This assistant cannot execute actions.</p>
    <div className="assistant-controls">
      <label>Briefing type <select value={task} disabled={busy} onChange={e => { setTask(e.target.value); setRecord(''); setResult(null); }}>
        <option value="network">Network summary</option><option value="recommendation">Explain recommendation</option><option value="incident">Summarize incident</option>
      </select></label>
      {task !== 'network' && <label>Source record <select value={record} disabled={busy} onChange={e => { setRecord(e.target.value); setResult(null); }}>
        <option value="">Choose a record</option>{records.data?.map(r => <option key={r.id} value={r.id}>{r.station_id || r.text || r.id}</option>)}
      </select></label>}
      <button className="button" disabled={busy || (task !== 'network' && !record)} onClick={explain}>{busy ? 'Preparing briefing…' : 'Generate briefing'}</button>
    </div>
    {records.isError && task !== 'network' && <p role="alert">Unable to load source records.</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
    {result && <div aria-live="polite">
      <p className="pill">{result.source === 'ai' ? 'Groq AI · verify against evidence' : 'Factual template'} · Tick {result.tick ?? 'unavailable'}</p>
      {outdated && <p role="status" className="notice">This briefing uses an older or unavailable snapshot. Generate a new briefing before acting.</p>}
      {result.fallback_reason && <p>{result.fallback_reason}</p>}
      <p className="assistant-answer">{result.explanation}</p><p className="subtle">{result.uncertainty}</p>
      <h3>Source evidence</h3>{result.sources.map(s => <details key={s.id}><summary>[{s.id}] {s.title}</summary><pre>{JSON.stringify(s.data, null, 2)}</pre></details>)}
    </div>}
  </section>;
}
