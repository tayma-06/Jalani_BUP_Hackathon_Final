import { lazy, Suspense, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, ArrowDownToLine, ArrowRight, Bell, Check, ChevronRight, CircleHelp, ClipboardList, Clock3, Droplets, Flag, Fuel, History, LayoutDashboard, LogOut, MapPin, Network as NetworkIcon, Play, RefreshCw, Settings2, ShieldCheck, Sparkles, TriangleAlert, Truck, Warehouse, Waves, X } from 'lucide-react';
import { api, label, number, percent, readSession } from './api';
import { fuels, type Alert, type AuditEntry, type Briefing, type Decision, type Forecast, type Health, type Incident, type Network, type Page, type Recommendation, type Session, type SimEvent, type Supply, type Tank, type Fuel as FuelType } from './types';

// recharts is the heaviest dependency in the app and is only needed on the forecast page, so
// the chart code is fetched on demand instead of blocking the first paint.
const DemandChart = lazy(() => import('./Charts').then(m => ({ default: m.DemandChart })));
const InventoryChart = lazy(() => import('./Charts').then(m => ({ default: m.InventoryChart })));

type Icon = typeof Activity;
const pages: { name: Page; icon: Icon; group: string }[] = [
  { name: 'Overview', icon: LayoutDashboard, group: 'Operations' }, { name: 'Stations & depots', icon: NetworkIcon, group: 'Operations' },
  { name: 'Recommendations', icon: Truck, group: 'Operations' }, { name: 'Alerts', icon: Bell, group: 'Operations' },
  { name: 'Supply & disruptions', icon: Waves, group: 'Intelligence' }, { name: 'Forecasts', icon: Activity, group: 'Intelligence' },
  { name: 'Decision history', icon: History, group: 'Intelligence' },
  { name: 'System health', icon: ShieldCheck, group: 'Platform' }, { name: 'Control room', icon: Settings2, group: 'Platform' },
  { name: 'Audit log', icon: ClipboardList, group: 'Platform' },
];
const groups = ['Operations', 'Intelligence', 'Platform'];
const descriptions: Record<Page, string> = {
  Overview: 'A clear view of the network. A better next decision.',
  'Stations & depots': 'Storage, demand and delivery commitments across every location.',
  Recommendations: 'Inspect the forecast. Review the impact. Approve the next move.',
  Alerts: 'Find the disruptions that need attention and track their recovery.',
  'Supply & disruptions': 'Follow incoming fuel and the events affecting its journey.',
  Forecasts: 'Explore demand, uncertainty and the next 12 hours of inventory.',
  'Decision history': 'A durable record of every shipment, operator and outcome.',
  'System health': 'Know when the platform is ready to make a decision.',
  'Control room': 'Run repeatable scenarios in the simulated environment.',
  'Audit log': 'Every approval, rejection and control action, with who did it.',
};

function useData<T>(path: string, enabled = true) {
  return useQuery<T>({ queryKey: [path], queryFn: () => api<T>(path), enabled, refetchInterval: 2000 });
}
const sentence = (value: string) => { const text = label(value).toLowerCase(); return text.charAt(0).toUpperCase() + text.slice(1); };
const shortName = (value: string) => value.replace(' Fuel Station', '');
// Same bands as the engine: HIGH from 50 %, MEDIUM from 20 %.
const tone = (risk?: number | null) => (risk || 0) >= 0.5 ? 'danger' : (risk || 0) >= 0.2 ? 'watch' : 'calm';

export function Badge({ value }: { value: string }) {
  const good = ['NORMAL', 'LOW', 'OPEN', 'AVAILABLE', 'ARRIVED', 'healthy', 'connected', 'RESOLVED'].includes(value);
  const bad = ['CRITICAL', 'HIGH', 'FAILED', 'DEGRADED', 'OUTAGE', 'DISRUPTED', 'down', 'unhealthy', 'UNKNOWN'].includes(value);
  return <span className={`badge ${good ? 'good' : bad ? 'bad' : 'warm'}`}><i />{sentence(value)}</span>;
}
function Empty({ children }: { children: ReactNode }) { return <div className="empty"><CircleHelp size={24} /><p>{children}</p></div>; }
function Panel({ title, detail, children, className = '' }: { title: string; detail?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><div className="panel-heading"><h2>{title}</h2>{detail}</div>{children}</section>;
}
/** Score ring: the arc fills with the value. Risk turns red as it grows; confidence reads the other way. */
function ScoreRing({ value, title, confidence = false }: { value?: number | null; title: string; confidence?: boolean }) {
  const v = Math.max(0, Math.min(1, value || 0)); const c = 2 * Math.PI * 15;
  const band = confidence ? (v >= 0.75 ? 'calm' : v >= 0.5 ? 'watch' : 'danger') : tone(v);
  return <span className={`score-ring ${band}`} role="img" aria-label={`${title} ${percent(value)}`}><svg viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="15" /><circle cx="18" cy="18" r="15" strokeDasharray={`${v * c} ${c}`} /></svg><b aria-hidden="true">{Math.round(v * 100)}</b></span>;
}
/** Shipment progress line: solid up to the marker, dotted after it, red when the journey runs into trouble. */
function Track({ progress, danger = false, marker: Marker = Truck, ends = true }: { progress: number; danger?: boolean; marker?: Icon | null; ends?: boolean }) {
  const p = Math.max(0, Math.min(1, progress)) * 100;
  return <div className={`track ${danger ? 'danger' : ''}`} aria-hidden="true">{ends && <Warehouse size={14} />}<span className="track-line"><i style={{ width: `${p}%` }} />{Marker && <em style={{ left: `${Math.min(94, Math.max(6, p))}%` }}><Marker size={12} /></em>}</span>{ends && <Flag size={14} />}</div>;
}

export default function App() {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<Session | null>(readSession);
  const [page, setPage] = useState<Page>('Overview');
  const [notice, setNotice] = useState('');
  const stateQuery = useData<Network>('/network/state', !!session);
  const healthQuery = useData<Health>('/health', !!session);
  const state = stateQuery.data;
  const [busy, setBusy] = useState(false);
  function logout() { sessionStorage.removeItem('jalani-session'); setSession(null); queryClient.clear(); }
  useEffect(() => {
    const expired = () => { sessionStorage.removeItem('jalani-session'); setSession(null); queryClient.clear(); };
    window.addEventListener('session-expired', expired);
    return () => window.removeEventListener('session-expired', expired);
  }, [queryClient]);
  async function action(path: string, body: unknown = {}) {
    setBusy(true); setNotice('');
    try {
      const result = await api<{ status?: string }>(path, 'POST', body);
      setNotice(result.status === 'UNKNOWN' ? 'Response uncertain. Review shipment history before sending another allocation.' : 'Action recorded. Refreshing the network.');
      await queryClient.invalidateQueries();
    } catch (error) { setNotice(error instanceof Error ? error.message : 'The action failed.'); }
    finally { setBusy(false); }
  }
  if (!session) return <Login onLogin={(value) => { sessionStorage.setItem('jalani-session', JSON.stringify(value)); setSession(value); }} />;
  const name = (id: string) => [...(state?.stations || []), ...(state?.depots || []), ...(state?.regions || [])].find(x => x.id === id)?.name || label(id);
  const canAct = session.role !== 'viewer';
  const blocked = !state || state.execution_blocked || stateQuery.isError || busy;
  const gateClosed = !state || state.execution_blocked || stateQuery.isError;
  const counts: Partial<Record<Page, number>> = { Recommendations: state?.kpis.pending_recommendations, Alerts: state?.kpis.open_alerts };
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  const simTime = state?.sim_time ? new Date(state.sim_time).toLocaleString('en-GB', { timeZone: 'UTC', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) + ' · sim UTC' : 'Awaiting simulator clock';
  const visible = pages.filter(p => p.name !== 'Control room' || session.role === 'admin');
  return <div className="app-shell">
    <aside className="sidebar"><div className="sidebar-card">
      <a className="brand" href="#" onClick={e => { e.preventDefault(); setPage('Overview'); }}><span className="brand-mark"><Droplets size={19} /></span><span className="brand-name">Jalani</span></a>
      <div className="workspace"><span className="workspace-icon"><MapPin size={16} /></span><div><strong>BUP fuel world</strong><small>{state ? `${state.depots.length} depots · ${state.stations.length} stations` : 'Connecting to the simulator'}</small></div></div>
      <nav aria-label="Main navigation">{groups.map(g => <div className="nav-group" key={g}><span className="nav-label">{g}</span>
        {visible.filter(p => p.group === g).map(p => <button key={p.name} className={page === p.name ? 'nav-item active' : 'nav-item'} onClick={() => setPage(p.name)}><p.icon size={18} /><span>{p.name}</span>{!!counts[p.name] && <b>{counts[p.name]}</b>}</button>)}
      </div>)}</nav>
      <div className="sidebar-bottom">
        <div className="assist-card"><div className="assist-head"><span className="assist-icon"><Sparkles size={16} /></span><div><strong>Jalani {state?.autopilot || 'advisory'}</strong><small>{gateClosed ? 'Execution paused' : 'Execution ready'} · {state?.policy_version || 'greedy_v1'}</small></div></div>
          <button onClick={() => setPage('Recommendations')}><span>{number(state?.kpis.pending_recommendations ?? 0)} awaiting review</span><ChevronRight size={16} /></button></div>
        <p className="sim-note"><ShieldCheck size={14} />Simulated environment · no real infrastructure</p>
        <div className="user"><span className="avatar">{session.username[0].toUpperCase()}</span><div><strong>{session.username}</strong><small>{session.role}</small></div><button className="icon-button" aria-label="Sign out" onClick={logout}><LogOut size={17} /></button></div>
      </div>
    </div></aside>
    <main>
      <header className="topbar">
        <div className="title-block"><span className="greeting">{greeting}, {session.username}</span><h1>{page === 'Overview' ? 'Network overview' : page}</h1></div>
        <div className="topbar-right">
          <span className="sim-label">SIMULATED ENVIRONMENT</span>
          <span className="chip"><Clock3 size={15} />Tick {number(state?.tick)}</span>
          <Badge value={state?.sim_status || 'CONNECTING'} />
          <button className="round-button" aria-label="Open alerts" onClick={() => setPage('Alerts')}><Bell size={18} />{!!state?.kpis.open_alerts && <i />}</button>
          {canAct && page !== 'Recommendations' && <button className="button primary" onClick={() => setPage('Recommendations')}>Review allocations <ArrowRight size={16} /></button>}
        </div>
      </header>
      <div className="page-content">
        {(stateQuery.isError || state?.mode !== 'NORMAL' || state?.stale) && <div className="status-banner" role="status"><span className="banner-icon"><TriangleAlert size={18} /></span><div className="banner-text"><strong>{stateQuery.isError ? 'Connection interrupted — showing the last available data.' : state?.reason || 'Connecting to the simulator.'}</strong><span>Allocation execution is paused. Data age: {number(state?.data_age_s, 1)} seconds.</span></div>{page !== 'System health' && <button className="button primary small" onClick={() => setPage('System health')}>Check health</button>}</div>}
        <div className="page-meta"><p>{descriptions[page]}</p><div className="meta-side"><Badge value={state?.mode || 'CONNECTING'} /><span>{simTime}</span><span className={`live-dot ${state?.stream === 'connected' ? '' : 'polling'}`}>{state?.stream === 'connected' ? 'Live stream + REST' : 'REST polling'} · {state?.autopilot || 'advisory'}</span></div></div>
        {notice && <div className="notice" role="status"><span>{notice}</span><button aria-label="Dismiss notification" onClick={() => setNotice('')}><X size={16} /></button></div>}
        {!state?.stations.length && page !== 'System health' && page !== 'Control room' ? <Panel title="Waiting for the fuel network"><Empty>Start the official simulator and check System health. The dashboard will populate after the first valid REST refresh.</Empty><button className="button primary" onClick={() => setPage('System health')}>View system health <ArrowRight size={16} /></button></Panel> : <>
          {page === 'Overview' && state && <Overview state={state} name={name} navigate={setPage} />}
          {page === 'Stations & depots' && state && <Stations state={state} />}
          {page === 'Recommendations' && <Recommendations state={state} name={name} canAct={canAct} blocked={blocked} action={action} />}
          {page === 'Alerts' && <Alerts canAct={canAct} busy={busy} action={action} />}
          {page === 'Supply & disruptions' && <SupplyPage state={state} name={name} />}
          {page === 'Forecasts' && state && <Forecasts state={state} />}
          {page === 'Decision history' && <HistoryPage name={name} canAct={canAct} blocked={blocked} busy={busy} action={action} />}
          {page === 'System health' && <HealthPage health={healthQuery.data} state={state} />}
          {page === 'Control room' && <ControlRoom state={state} busy={busy} action={action} />}
          {page === 'Audit log' && <AuditLog name={name} />}
        </>}
        <footer><span>Jalani · Decisions with context.</span><span>v{healthQuery.data?.version || '0.1.0'} · {healthQuery.data?.git_sha?.slice(0, 8) || 'dev'} · All fuel values in litres</span></footer>
      </div>
    </main>
  </div>;
}

function Login({ onLogin }: { onLogin: (value: Session) => void }) {
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('');
  const [error, setError] = useState(''); const [loading, setLoading] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault(); setLoading(true); setError('');
    try { onLogin(await api<Session>('/auth/login', 'POST', { username, password })); }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to sign in'); }
    finally { setLoading(false); }
  }
  return <div className="login">
    <div className="login-story"><div className="brand"><span className="brand-mark"><Droplets size={19} /></span><span className="brand-name">Jalani</span></div>
      <div><span className="eyebrow">Fuel supply intelligence</span><h1>Keep the network<br />moving forward.</h1><p>See shortages before they happen.<br />Make every allocation count.</p>
        <div className="login-flow"><span><Fuel size={16} /> Observe</span><ArrowRight size={16} /><span><Activity size={16} /> Predict</span><ArrowRight size={16} /><span><Truck size={16} /> Act</span></div></div>
      <small>BUP HACKATHON FINALS · SIMULATED ENVIRONMENT</small></div>
    <div className="login-panel"><form onSubmit={submit}><span className="eyebrow">Operator workspace</span><h2>Welcome to the control room.</h2><p className="lead">Sign in with your configured deployment credentials.</p>
      <label>Username<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required /></label>
      <label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
      {error && <p className="error-text" role="alert">{error}</p>}
      <button className="button primary wide" disabled={loading}>{loading ? 'Signing in…' : 'Enter workspace'}<ArrowRight size={17} /></button>
      <div className="login-hint"><ShieldCheck size={16} /> Role-based access · Audited decisions</div></form></div>
  </div>;
}

function Overview({ state, name, navigate }: { state: Network; name: (id: string) => string; navigate: (page: Page) => void }) {
  const incidents = useData<Incident[]>('/incidents');
  const [summary, setSummary] = useState<Briefing | null>(null);
  const [summaryError, setSummaryError] = useState('');
  const ranked = state.stations.flatMap(s => fuels.map(f => ({ station: s, fuel: f, ...s.fuels[f] }))).sort((a, b) => (b.risk || 0) - (a.risk || 0));
  const risks = ranked.slice(0, 5);
  const top = ranked[0];
  const cards = [
    { label: 'Network service level', value: percent(state.kpis.service_level), note: 'of demand served', highlight: true },
    { label: 'Unmet demand', value: number(state.kpis.unmet_liters), note: 'litres this run' },
    { label: 'Fuel on the move', value: number(state.kpis.in_transit_liters), note: 'litres in transit' },
    { label: 'Awaiting review', value: number(state.kpis.pending_recommendations), note: 'proposals' },
    { label: 'Open alerts', value: number(state.kpis.open_alerts), note: 'operational' },
    { label: 'Tanks at high risk', value: number(ranked.filter(r => (r.risk || 0) >= 0.5).length), note: `of ${ranked.length} station tanks` },
  ];
  async function summarize() { try { setSummaryError(''); setSummary(await api<Briefing>('/summary')); } catch (e) { setSummaryError(String(e)); } }
  return <>
    <div className="kpi-grid">{cards.map(c => <div key={c.label} className={`kpi ${c.highlight ? 'highlight' : ''}`}><span>{c.label}</span><div className="kpi-value"><strong>{c.value}</strong><small>{c.note}</small></div></div>)}</div>
    <div className="grid-main">
      <Panel title="Fuel network" className="map-panel" detail={<div className="row"><span className="pill">{state.depots.length} depots · {state.stations.length} stations</span><button className="text-button" onClick={() => navigate('Stations & depots')}>Explore <ArrowRight size={14} /></button></div>}>
        <div className="map-frame"><NetworkMap state={state} />
          <div className="map-foot"><div className="map-legend"><span><i className="line" />Available</span><span><i className="line red" />Disrupted</span><span><i className="line dotted" />Cross-region</span></div>
            {top && (top.risk || 0) >= 0.5 && <div className="danger-row"><TriangleAlert size={16} /><span><b>{shortName(top.station.name)} · {top.fuel}</b> {top.hours_to_stockout == null ? 'high shortage risk' : `runs short in ${number(top.hours_to_stockout, 1)}\u00a0h`}</span><button className="button primary small" onClick={() => navigate('Recommendations')}>Review</button></div>}</div></div>
        <div className="panel-bottom"><MapPin size={14} />Schematic view · routes discovered from the simulator</div>
      </Panel>
      <Panel title="Priority watch" detail={<span className="pill">Next 12 hours</span>}>
        <div className="watch-list">{risks.map(r => <div className="watch-row" key={r.station.id + r.fuel}>
          <div className="watch-head"><span className="code">{shortName(name(r.station.id))} · {r.fuel}</span><span className={`tag ${tone(r.risk)}`}>{tone(r.risk) === 'danger' ? 'Action needed' : tone(r.risk) === 'watch' ? 'Watch' : 'Stable'}</span><ScoreRing value={r.risk} title="Stockout risk" /></div>
          <div className="watch-ends"><span>{number(r.inventory)} L now</span><span>{r.hours_to_stockout == null ? 'Beyond forecast horizon' : `${number(r.hours_to_stockout, 1)}h to shortage`}</span></div>
          <Track progress={r.hours_to_stockout == null ? 1 : r.hours_to_stockout / 12} danger={r.hours_to_stockout != null} marker={r.hours_to_stockout == null ? null : Fuel} ends={false} />
        </div>)}</div>
        <p className="fineprint">Stockout estimates use 300 demand paths by default. They are model estimates, not guaranteed outcomes.</p>
        <button className="button wide" onClick={() => navigate('Recommendations')}>Review recommended moves <ArrowRight size={15} /></button>
      </Panel>
    </div>
    <div className="grid-three">
      <Panel title="Regional demand" detail={<span className="subtle">Observed · last simulator hour</span>}>
        {state.regions.map(r => { const max = Math.max(1, ...fuels.map(f => Math.max(r.fuels[f].demand_last_hour, r.fuels[f].normal_last_hour))); return <div className="region" key={r.id}>
          <div className="region-head"><span className="pill dot">{r.name}</span><b>{number(fuels.reduce((sum, f) => sum + r.fuels[f].demand_last_hour, 0))} L</b></div>
          {fuels.map(f => { const d = r.fuels[f]; const surge = d.normal_last_hour > 0 && d.demand_last_hour > d.normal_last_hour * 1.15; return <div className="funnel-row" key={f}><span>{f}</span><div className="funnel-track"><i className={surge ? 'surge' : ''} style={{ width: `${Math.min(100, d.demand_last_hour / max * 100)}%` }} />{d.normal_last_hour > 0 && <em style={{ left: `${Math.min(100, d.normal_last_hour / max * 100)}%` }} title={`Normal: ${number(d.normal_last_hour)} L`} />}</div><b>{number(d.demand_last_hour)}</b></div>; })}
        </div>; })}
        <p className="fineprint">Tick mark: normal demand for this hour. Yellow: more than 15% above normal.</p>
      </Panel>
      <section className="panel assist-panel">
        <div className="assist-top"><span className="tag ink"><Sparkles size={12} />Operations briefing</span><small title={summary?.reason}>{summary?.source === 'llm' ? 'Written by Claude · numbers checked' : 'Grounded template'}</small></div>
        <h2>Every signal has a next step.</h2>
        <p>{summary?.text || 'Get a concise summary of service level, open alerts and allocations ready for review.'}</p>
        <button className="suggest" onClick={summarize}><span><small>Suggested action</small><strong>Summarize network</strong></span><i><ArrowRight size={16} /></i></button>
        {summaryError && <p className="error-text">{summaryError}</p>}
        <div className="assist-note"><ShieldCheck size={16} /><span>Crisis, cross-region and rationing moves always wait for a human operator.</span></div>
      </section>
      <Panel title="Incidents" detail={<span className="pill">{incidents.data?.length || 0}</span>}>
        {incidents.data?.slice(0, 4).map(i => <div className="needs-row" key={i.id}><span className={`needs-icon ${i.severity?.toLowerCase() || ''}`}><Activity size={15} /></span><p>{i.text}</p><small>Tick {i.tick}</small></div>)}
        {!incidents.data?.length && <Empty>No incidents detected in the current run.</Empty>}
      </Panel>
    </div>
  </>;
}

function NetworkMap({ state }: { state: Network }) {
  const h = Math.max(400, state.stations.length * 95);
  const yDepot = (i: number) => 45 + (i + 0.5) * (h - 90) / state.depots.length;
  const yStation = (i: number) => 35 + (i + 0.5) * (h - 70) / state.stations.length;
  const arc = 2 * Math.PI * 10;
  return <svg className="network-map" viewBox={`0 0 720 ${h}`} role="img" aria-label="Fuel network showing depots, stations and route status">
    <text x="35" y="27" className="map-heading">STORAGE & DISPATCH</text><text x="482" y="27" className="map-heading">STATION NETWORK</text>
    {state.routes.map(r => { const di = state.depots.findIndex(d => d.id === r.source_depot_id); const si = state.stations.findIndex(s => s.id === r.destination_station_id); if (di < 0 || si < 0) return null; const cross = state.depots[di].region_id !== state.stations[si].region_id; const down = r.status === 'DISRUPTED'; return <g key={r.id}><path d={`M 232 ${yDepot(di)} C 350 ${yDepot(di)}, 365 ${yStation(si)}, 482 ${yStation(si)}`} fill="none" stroke={down ? '#e5484d' : cross ? '#9d998f' : '#141412'} strokeWidth={cross ? 1.4 : 1.8} strokeDasharray={down ? '6 5' : cross ? '1.5 5' : ''} strokeLinecap="round"><title>{r.id}: {r.status}, {r.transit_ticks} ticks</title></path><circle cx="232" cy={yDepot(di)} r="3.5" fill="#141412" /><circle cx="482" cy={yStation(si)} r="3.5" fill="#fff" stroke={down ? '#e5484d' : '#141412'} strokeWidth="1.5" /></g>; })}
    {state.depots.map((d, i) => <g key={d.id} transform={`translate(35, ${yDepot(i) - 30})`}><rect width="197" height="60" rx="16" fill="#141412" /><rect x="12" y="14" width="32" height="32" rx="16" fill="#eef05b" /><path d="M20 37 V26 L28 21 L36 26 V37 Z M25 37 V31 H31 V37" fill="none" stroke="#141412" strokeWidth="1.6" strokeLinejoin="round" /><text x="54" y="27" fill="#fff" className="map-name">{d.name}</text><text x="54" y="44" fill="#b3b0a7" className="map-small">{number(fuels.reduce((s, f) => s + d.fuels[f].inventory, 0))} L in storage</text></g>)}
    {state.stations.map((s, i) => { const risk = Math.max(...fuels.map(f => s.fuels[f].risk || 0)); const colour = s.status === 'OUTAGE' || risk >= 0.5 ? '#e5484d' : risk >= 0.2 ? '#c58a12' : '#3b8f55'; return <g key={s.id} transform={`translate(482, ${yStation(i) - 28})`}><rect width="205" height="56" rx="16" fill="#fff" stroke="#e2dfd8" /><circle cx="26" cy="28" r="10" fill="none" stroke="#ecebe6" strokeWidth="3" /><circle cx="26" cy="28" r="10" fill="none" stroke={colour} strokeWidth="3" strokeLinecap="round" strokeDasharray={`${Math.max(0.02, risk) * arc} ${arc}`} transform="rotate(-90 26 28)" />{s.status === 'OUTAGE' && <circle cx="26" cy="28" r="4" fill="#e5484d" />}<text x="46" y="24" className="map-name" fill="#141412">{shortName(s.name)}</text><text x="46" y="41" className="map-small" fill="#8a877f">{number(fuels.reduce((a, f) => a + s.fuels[f].inventory, 0))} L · {percent(risk)} risk</text></g>; })}
  </svg>;
}

function Stations({ state }: { state: Network }) {
  return <><div className="section-label">Retail stations <span>{state.stations.length} locations</span></div><div className="tank-grid">{state.stations.map(s => <TankCard key={s.id} tank={s} />)}</div><div className="section-label">Supply depots <span>{state.depots.length} locations</span></div><div className="tank-grid">{state.depots.map(s => <TankCard key={s.id} tank={s} depot />)}</div></>;
}
function TankCard({ tank, depot = false }: { tank: Tank; depot?: boolean }) {
  const worst = Math.max(...fuels.map(f => tank.fuels[f].risk || 0));
  return <Panel title={tank.name} detail={<div className="row">{!depot && <ScoreRing value={worst} title="Highest stockout risk" />}<Badge value={tank.status} /></div>}>
    <div className="tank-meta">{depot ? `${number(tank.dispatch_used)} / ${number(tank.dispatch_capacity_per_tick)} L dispatch committed` : `Demand multiplier ×${number(tank.demand_multiplier, 2)}`}</div>
    {fuels.map(f => { const v = tank.fuels[f]; return <div className="tank-fuel" key={f}>
      <div className="tank-line"><span className="code">{f}</span><span><b>{number(v.inventory)}</b> <small>/ {number(v.capacity)} L</small></span></div>
      <div className="fuel-track"><i className={!depot && (v.risk || 0) >= 0.5 ? 'low' : f.toLowerCase()} style={{ width: `${Math.min(100, v.inventory / v.capacity * 100)}%` }} /></div>
      {!depot && <div className="fuel-details"><span>{v.hours_to_stockout == null ? 'Beyond 12h horizon' : `${number(v.hours_to_stockout, 1)}h to stockout`}</span><Badge value={v.risk_level || 'UNKNOWN'} /></div>}
      {!!v.in_transit && <span className="incoming">+{number(v.in_transit)} L incoming</span>}
    </div>; })}
  </Panel>;
}

type Action = (path: string, body?: unknown) => Promise<void>;
function Recommendations({ state, name, canAct, blocked, action }: { state?: Network; name: (id: string) => string; canAct: boolean; blocked: boolean; action: Action }) {
  const query = useData<Recommendation[]>('/recommendations');
  return <><div className="info-strip"><span className="strip-icon"><ShieldCheck size={17} /></span><span>Each quantity accounts for depot stock, route limits, tank capacity and committed shipments. Impact is a model estimate.</span><span className="pill ink">{state?.policy_version || 'greedy_v1'}</span></div>{query.isError && <p className="error-text">Recommendations could not refresh. Cached proposals are shown.</p>}{!query.data?.length ? <Panel title="Allocation queue"><Empty>No allocations currently need review. Recommendations appear when forecast risk or low inventory makes a delivery useful.</Empty></Panel> : <div className="recommendations-grid">{query.data.map(r => <RecommendationCard key={r.id} recommendation={r} name={name} canAct={canAct} blocked={blocked} action={action} />)}</div>}</>;
}
export function RecommendationCard({ recommendation: r, name, canAct, blocked, action }: { recommendation: Recommendation; name: (id: string) => string; canAct: boolean; blocked: boolean; action: Action }) {
  const [quantity, setQuantity] = useState(r.action.quantity); const [rejecting, setRejecting] = useState(false); const [reason, setReason] = useState('');
  const station = name(r.station_id);
  return <article className="panel recommendation">
    <div className="rec-top"><ScoreRing value={r.confidence.score} title="Confidence" confidence /><span className="code">{shortName(station)} · {r.fuel_type}</span><span className="pill">{sentence(r.confidence.level)} confidence</span></div>
    <h2>{station}</h2>
    <div className="rec-route"><div><small>From</small><strong>{name(r.action.source_depot_id)}</strong></div><div className="dest"><small>ETA tick {r.action.eta_tick}</small><strong>{shortName(station)}</strong></div></div>
    <Track progress={0} />
    <div className="rec-amount">{number(r.action.quantity)}<span>litres recommended</span></div>
    <div className="impact"><div><small>Stockout risk</small><strong>{percent(r.impact.risk_before)}<ArrowRight size={17} /><em>{percent(r.impact.risk_after)}</em></strong></div><div><small>Expected unmet fuel</small><strong>{number(r.impact.unmet_before_l)} <span>→ {number(r.impact.unmet_after_l)} L</span></strong></div></div>
    {r.requires_review && <div className="review-note"><TriangleAlert size={15} /><span>Human review required · consequential or uncertain decision</span></div>}
    <details><summary>Why this allocation?<ChevronRight size={16} /></summary><p>{r.explanation}</p><h4>Signals</h4><ul>{r.signals.map(x => <li key={x}>{x}</li>)}</ul><h4>Constraints checked</h4><ul>{r.constraints.map(x => <li key={x}>{x}</li>)}</ul><h4>Alternatives</h4><div className="alt-grid">{r.alternatives.map(x => <div className="alternative" key={x.label}><span>{x.label}</span><b>{percent(x.risk_after)} risk</b></div>)}</div><small>Confidence: {percent(r.confidence.score)} heuristic · {r.model_version} · {r.explanation_source}</small></details>
    {canAct && <div className="rec-actions"><label>Shipment quantity (L)<input aria-label={`Quantity for ${station} ${r.fuel_type}`} type="number" min="1" max={r.action.quantity} value={quantity} onChange={e => setQuantity(Number(e.target.value))} /></label><div><button className="button primary" disabled={blocked || !Number.isFinite(quantity) || quantity <= 0 || quantity > r.action.quantity} onClick={() => action(`/recommendations/${r.id}/approve`, { quantity })}><Check size={16} />Approve shipment</button><button className="button ghost" disabled={blocked} onClick={() => setRejecting(!rejecting)}>Reject</button></div>{rejecting && <form onSubmit={e => { e.preventDefault(); void action(`/recommendations/${r.id}/reject`, { reason }); }}><input aria-label="Rejection reason" placeholder="Reason for rejecting" required value={reason} onChange={e => setReason(e.target.value)} /><button className="button" disabled={blocked || !reason.trim()}>Confirm rejection</button></form>}</div>}
  </article>;
}

function Alerts({ canAct, busy, action }: { canAct: boolean; busy: boolean; action: Action }) {
  const query = useData<Alert[]>('/alerts'); const [status, setStatus] = useState('ALL');
  const all = query.data || [];
  const items = all.filter(a => status === 'ALL' || a.status === status);
  return <Panel title="Operational alerts" detail={<div className="segmented" role="group" aria-label="Filter alerts">{['ALL', 'OPEN', 'ACKNOWLEDGED', 'RESOLVED'].map(x => <button key={x} className={status === x ? 'active' : ''} aria-pressed={status === x} onClick={() => setStatus(x)}>{sentence(x)}<b>{x === 'ALL' ? all.length : all.filter(a => a.status === x).length}</b></button>)}</div>}>
    {!items.length ? <Empty>No alerts match this view.</Empty> : <div className="alert-list">{items.map(a => <div className="alert-row" key={a.id}><span className={`alert-icon ${a.severity.toLowerCase()}`}><Bell size={18} /></span><div className="grow"><div className="row"><h3>{sentence(a.type)}</h3><Badge value={a.severity} /></div><p>{a.message}</p><small>First seen tick {a.first_tick} · Last seen tick {a.last_tick}{a.event_ids.length ? ` · Active events: ${a.event_ids.join(', ')}` : ''}</small></div><div className="alert-action"><Badge value={a.status} />{canAct && a.status === 'OPEN' && <button disabled={busy} className="button small" onClick={() => action(`/alerts/${encodeURIComponent(a.id)}/ack`)}>Acknowledge</button>}</div></div>)}</div>}
  </Panel>;
}

function AuditLog({ name }: { name: (id: string) => string }) {
  const [pages, setPages] = useState<AuditEntry[][]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  // Loaded on demand rather than polled: the trail changes only when an operator acts, and the
  // dashboard already refetches twice a second for everything else.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const first = await api<AuditEntry[]>('/audit?limit=100');
        if (!cancelled) { setPages([first]); setError(''); }
      } catch (e) { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load the audit log.'); }
    })();
    return () => { cancelled = true; };
  }, []);
  async function older() {
    const page = pages[pages.length - 1];
    const last = page?.[page.length - 1]?.id;
    if (!last) return;
    setLoading(true);
    try {
      const next = await api<AuditEntry[]>(`/audit?limit=100&before=${last}`);
      setPages([...pages, next]); setError('');
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not load older entries.'); }
    finally { setLoading(false); }
  }
  const entries = pages.flat();
  const exhausted = pages[pages.length - 1]?.length === 0;
  // Audit actions are dotted (`decision.approved`); read them as a sentence.
  const action = (value: string) => sentence(value.replaceAll('.', ' '));
  return <Panel title="Audit log" detail={<span className="subtle">{entries.length} entries, newest first</span>}>
    {error && <p className="fineprint" role="alert">{error}</p>}
    {!entries.length && !error ? <Empty>No actions have been recorded yet.</Empty> : <div className="audit-list">{entries.map(e => <div className="audit-row" key={e.id}><span className="audit-icon"><ClipboardList size={17} /></span><div className="grow"><div className="row"><h3>{action(e.action)}</h3><Badge value={e.actor} /></div><p className="audit-target">{name(e.target) !== e.target ? `${name(e.target)} · ` : ''}{e.target}</p><small>{new Date(e.created_at).toLocaleString('en-GB', { timeZone: 'UTC' })} UTC · {Object.keys(e.details || {}).length} detail field(s)</small></div></div>)}</div>}
    <div className="panel-bottom">{exhausted ? 'That is the whole retained trail.' : <button className="button small" disabled={loading} onClick={older}>{loading ? 'Loading…' : 'Load older entries'}</button>}</div>
  </Panel>;
}

function SupplyTimeline({ items, tick, name }: { items: Supply[]; tick: number; name: (id: string) => string }) {
  const start = Math.min(tick, ...items.map(s => s.planned_tick)); const end = Math.max(tick + 1, ...items.map(s => s.planned_tick));
  const at = (t: number) => (t - start) / Math.max(1, end - start) * 100;
  const x = (t: number) => `${at(t)}%`;
  const depots = [...new Set(items.map(s => s.depot_id))];
  return <Panel title="Supply timeline" detail={<div className="chart-legend">{fuels.map(f => <span key={f}><i className={`swatch ${f.toLowerCase()}`} />{f}</span>)}<span><i className="swatch delayed" />Delayed</span></div>}>
    <div className="timeline"><div className="timeline-labels">{depots.map(d => <span key={d}>{name(d)}</span>)}</div>
      <div className="timeline-body"><div className="timeline-axis">{[0, 0.25, 0.5, 0.75, 1].map(p => <span key={p} style={{ left: `${p * 100}%` }}>{Math.round(start + p * (end - start))}</span>)}</div>
        {depots.map(d => <div className="timeline-lane" key={d}>{items.filter(s => s.depot_id === d).map(s => <i key={s.id} className={`${s.fuel_type.toLowerCase()} ${s.status.toLowerCase()}`} style={{ left: x(s.planned_tick), top: `${28 + fuels.indexOf(s.fuel_type) * 22}%` }} title={`${s.id}: ${number(s.quantity)} L ${s.fuel_type} at tick ${s.planned_tick} · ${label(s.status)}`} />)}</div>)}
        <div className={`timeline-now ${at(tick) > 85 ? 'end' : at(tick) < 15 ? 'start' : ''}`} style={{ left: x(tick) }}><b>Now · {tick}</b></div></div></div>
    <p className="fineprint">Each dot is a scheduled ship arrival at a depot. Faded dots have arrived. Supply is finite: after the last dot, depots only drain.</p>
  </Panel>;
}
function SupplyPage({ state, name }: { state?: Network; name: (id: string) => string }) {
  const supply = useData<Supply[]>('/supply'); const events = useData<SimEvent[]>('/events');
  const pending = supply.data?.filter(s => s.status !== 'ARRIVED') || [];
  const lastTick = pending.length ? Math.max(...pending.map(s => s.planned_tick)) : null;
  return <><div className="info-strip"><span className="strip-icon"><Waves size={17} /></span><span>{lastTick === null ? 'No further scheduled supply is visible.' : `The last scheduled supply arrives at tick ${lastTick} — ${Math.max(0, lastTick - (state?.tick || 0))} ticks from now.`} Supply is finite.</span></div>
    {!!supply.data?.length && <SupplyTimeline items={supply.data} tick={state?.tick || 0} name={name} />}
    <Panel title="Incoming supply"><div className="table-wrap"><table><thead><tr><th>Shipment</th><th>Destination</th><th>Fuel</th><th>Quantity</th><th>Planned tick</th><th>Status</th></tr></thead><tbody>{supply.data?.map(s => <tr key={s.id}><td className="code">{s.id}</td><td>{name(s.depot_id)}</td><td><span className="fuel-tag">{s.fuel_type}</span></td><td>{number(s.quantity)} L</td><td>{s.planned_tick}</td><td><Badge value={s.status} /></td></tr>)}</tbody></table></div>{!supply.data?.length && <Empty>No supply records available.</Empty>}</Panel>
    <div className="grid-two"><Panel title="Domain events">{events.data?.length ? <div className="rows">{events.data.map(e => <div className="event-row" key={e.id}><div><h3>{sentence(e.type)}</h3><small>Ticks {e.start_tick}–{e.end_tick}</small><pre>{JSON.stringify(e.parameters, null, 2)}</pre></div><Badge value={e.status} /></div>)}</div> : <Empty>No domain events have been injected.</Empty>}</Panel>
      <Panel title="Route availability"><div className="rows">{state?.routes.map(r => <div className="route-row" key={r.id}><div><strong>{name(r.source_depot_id)}<ArrowRight size={12} />{name(r.destination_station_id)}</strong><small>{r.transit_ticks} transit ticks · maximum {number(r.max_shipment)} L / shipment</small></div><Badge value={r.status} /></div>)}</div></Panel></div></>;
}

function Forecasts({ state }: { state: Network }) {
  const [station, setStation] = useState(state.stations[0]?.id || ''); const [fuel, setFuel] = useState<FuelType>('DIESEL');
  const query = useData<Forecast>(`/stations/${station}/forecast?fuel=${fuel}`, !!station);
  const f = query.data;
  const history = f?.history.slice(-24).map(h => ({ tick: h.tick, actual: h.demand_liters })) || [];
  const future = f?.mean.slice(0, f.inventory_path.length).map((mean, i) => ({ tick: f.start_tick + i + 1, forecast: mean, lower: Math.max(0, mean - 1.96 * f.std[i]), upper: mean + 1.96 * f.std[i], inventory: f.inventory_path[i], band: [Math.max(0, mean - 1.96 * f.std[i]), mean + 1.96 * f.std[i]] })) || [];
  return <><div className="filter-bar"><label>Station<select value={station} onChange={e => setStation(e.target.value)}>{state.stations.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label><div className="field"><span>Fuel</span><div className="segmented" role="group" aria-label="Fuel">{fuels.map(s => <button key={s} type="button" className={fuel === s ? 'active' : ''} aria-pressed={fuel === s} onClick={() => setFuel(s)}>{s}</button>)}</div></div><span className="pill">{f?.model_version || 'Loading model'}</span></div>
    {f && <div className="stat-strip"><div><span>Stockout risk · 12 h</span><strong className={tone(f.risk)}>{percent(f.risk)}</strong><small>Share of simulated demand paths that run dry</small></div><div><span>Mean-path shortage</span><strong>{f.hours_to_stockout == null ? 'None' : number(f.hours_to_stockout, 1)}{f.hours_to_stockout != null && <small> h</small>}</strong><small>{f.hours_to_stockout == null ? 'Within the forecast horizon' : 'Until the expected path hits zero'}</small></div><div><span>Forecast demand</span><strong>{number(future.reduce((sum, p) => sum + p.forecast, 0))}<small> L</small></strong><small>Summed over the forecast horizon</small></div></div>}
    <Panel title="Demand forecast" detail={<span className="subtle">Litres / simulator tick</span>}>{f ? <><div className="chart-legend"><span><i className="line" />Observed demand</span><span><i className="line dashed" />Forecast</span><span><i className="swatch band" />Approximate 95% demand band</span></div><div className="chart"><Suspense fallback={<Empty>Loading charts…</Empty>}><DemandChart data={[...history, ...future]} /></Suspense></div><p className="fineprint">{f.source}. {f.uncertainty_note}</p></> : <Empty>Forecast not available yet.</Empty>}</Panel>
    <Panel title="Projected fuel remaining" detail={<Badge value={(f?.risk || 0) >= 0.5 ? 'HIGH' : 'LOW'} />}><div className="chart"><Suspense fallback={<Empty>Loading charts…</Empty>}><InventoryChart data={future} /></Suspense></div><div className="panel-bottom">Includes pending and in-transit allocations · {f?.hours_to_stockout == null ? 'No mean-path shortage within the forecast horizon' : `Mean-path shortage in ${number(f.hours_to_stockout, 1)} hours`}</div></Panel></>;
}

// How far along its journey each stored shipment status is drawn.
const journey: Record<string, number> = { PREPARED: 0.05, PENDING: 0.15, IN_TRANSIT: 0.55, ARRIVED: 1 };
function HistoryPage({ name, canAct, blocked, busy, action }: { name: (id: string) => string; canAct: boolean; blocked: boolean; busy: boolean; action: Action }) {
  const query = useData<Decision[]>('/decisions'); const [error, setError] = useState('');
  async function download() {
    try { const r = await fetch('/api/decisions/export.csv', { headers: { Authorization: `Bearer ${readSession()?.access_token}` } }); if (!r.ok) throw new Error('CSV export failed'); const url = URL.createObjectURL(await r.blob()); const a = document.createElement('a'); a.href = url; a.download = 'jalani-decisions.csv'; a.click(); URL.revokeObjectURL(url); } catch (e) { setError(String(e)); }
  }
  return <Panel title="Shipment ledger" detail={<button className="button primary" onClick={download}><ArrowDownToLine size={15} />Export CSV</button>}>{error && <p className="error-text">{error}</p>}{!query.data?.length ? <Empty>Approved shipments will appear here with their immutable request and simulator outcome.</Empty> : <div className="decision-list">{query.data.map(d => { const trouble = ['FAILED', 'UNKNOWN', 'CANCELLED', 'ABANDONED_RESET'].includes(d.status); return <div className="decision" key={d.id}>
    <div className="decision-head"><span className="code">{d.request.route_id}</span><span className="fuel-tag">{d.request.fuel_type}</span><Badge value={d.status} /><span className="grow" /><strong className="decision-qty">{number(d.request.quantity)} L</strong></div>
    <div className="rec-route"><div><small>From</small><strong>{name(d.request.source_depot_id)}</strong></div><div className="dest"><small>To</small><strong>{name(d.request.destination_station_id)}</strong></div></div>
    <Track progress={journey[d.status] ?? 0.05} danger={trouble} />
    <p className="subtle">{d.actor} · {new Date(d.created_at).toLocaleString()} · Simulator #{d.sim_id ?? 'unconfirmed'}</p>
    {d.failure_reason && <div className="review-note danger"><TriangleAlert size={15} /><span>{d.failure_reason}</span></div>}
    <details><summary>Audit details <ChevronRight size={15} /></summary><p>Run: {d.run_id}</p><p>Route: {d.request.route_id}</p><p className="break">Immutable key: {d.request.idempotency_key}</p></details>
    {canAct && d.status === 'UNKNOWN' && <button className="button primary" disabled={busy} onClick={() => action(`/decisions/${d.id}/retry`)}><RefreshCw size={15} />Reconcile and retry original request</button>}
    {canAct && d.status === 'PENDING' && <button className="button" disabled={blocked} onClick={() => action(`/allocations/${d.sim_id}/cancel`)}>Cancel pending shipment</button>}
  </div>; })}</div>}</Panel>;
}

function HealthPage({ health, state }: { health?: Health; state?: Network }) {
  const paused = !state || state.execution_blocked;
  return <><div className="stat-strip four"><div><span>API p95 latency</span><strong>{number(health?.p95_latency_ms, 1)}<small> ms</small></strong><small>Rolling 1,000 application requests</small></div><div><span>Server error rate</span><strong>{percent(health?.error_rate)}</strong><small>Rolling requests · HTTP 5xx</small></div><div><span>Snapshot age</span><strong>{number(state?.data_age_s, 1)}<small> s</small></strong><small>Since last validated REST refresh</small></div><div className={`gate ${paused ? 'paused' : 'ready'}`}><span>Execution gate</span><strong>{paused ? 'Paused' : 'Ready'}</strong><small>{paused ? 'Review data and dependency health' : 'Fresh state and durable audit available'}</small></div></div>
    <Panel title="Component health"><div className="table-wrap"><table><thead><tr><th>Component</th><th>Status</th><th>Details</th></tr></thead><tbody>{Object.entries(health?.components || {}).map(([key, value]) => <tr key={key}><td className="capitalize">{key === 'llm' ? 'LLM' : label(key)}</td><td><Badge value={value.status} /></td><td>{Object.entries(value).filter(([k]) => k !== 'status').map(([k, v]) => `${label(k)}: ${v ?? 'not measured'}`).join(' · ') || 'Responding'}</td></tr>)}</tbody></table></div></Panel>
    <div className="grid-two"><Panel title="Observability tools"><p>Use the provisioned dashboards for request latency, inventory, stockout risk and service recovery.</p><div className="row"><a className="button primary" href="http://localhost:3001" target="_blank" rel="noreferrer">Local Grafana <ArrowRight size={15} /></a><a className="button" href="http://localhost:9090" target="_blank" rel="noreferrer">Local Prometheus <ArrowRight size={15} /></a></div><p className="fineprint">These addresses are for a local Compose deployment. Remote deployments should forward the configured monitoring ports.</p></Panel><Panel title="Readiness & recovery"><p>Liveness means this process can respond. Readiness also requires fresh, valid simulator data, a usable database and resolved shipment outcomes.</p><p className="subtle">History completeness: {state?.history_gap ? 'A retrieval gap was detected' : 'No gap detected in the latest retrieval'}. Model probabilities require calibration against the official simulator.</p></Panel></div></>;
}

const clockLabels: Record<string, string> = { run: 'Run', pause: 'Pause', step: 'Step one tick' };
function ControlRoom({ state, busy, action }: { state?: Network; busy: boolean; action: Action }) {
  const [event, setEvent] = useState('demand_spike'); const [target, setTarget] = useState('');
  const [duration, setDuration] = useState(16); const [magnitude, setMagnitude] = useState(1.8);
  const [fault, setFault] = useState('stale_data'); const [faultDuration, setFaultDuration] = useState(30);
  const [faultValue, setFaultValue] = useState(500); const [reset, setReset] = useState(false);
  const targetItems = event === 'route_disruption' ? state?.routes.map(r => ({ id: r.id, name: label(r.id) })) : event === 'station_outage' ? state?.stations : event === 'demand_spike' ? state?.regions : state?.depots;
  function inject(e: FormEvent) {
    e.preventDefault(); const key = event === 'route_disruption' ? 'route_ids' : event === 'station_outage' ? 'station_ids' : event === 'demand_spike' ? 'region_ids' : 'depot_ids';
    const parameters: Record<string, unknown> = { [key]: target ? [target] : [] };
    if (event === 'demand_spike') parameters.multiplier = magnitude;
    if (event === 'shipment_delay') parameters.delay_ticks = Math.round(magnitude);
    if (event === 'supply_shortfall') parameters.factor = magnitude;
    void action('/control/events', { type: event, start_tick: (state?.tick || 0) + 1, duration_ticks: duration, parameters });
  }
  return <><div className="info-strip"><span className="strip-icon"><Settings2 size={17} /></span><span>Administrator controls affect only the connected simulator. Pause and step for repeatable demonstrations.</span></div>
    <Panel title="Simulation clock" detail={<Badge value={state?.sim_status || 'UNKNOWN'} />}><div className="control-buttons">{['run', 'pause', 'step'].map(a => <button className={`button ${a === 'run' ? 'primary' : ''}`} disabled={busy} key={a} onClick={() => action(`/control/sim/${a}`)}>{a === 'run' && <Play size={15} />}{clockLabels[a]}</button>)}<button className="button danger-button" onClick={() => setReset(!reset)}>Reset simulator</button></div>{reset && <div className="confirm"><p>Reset deletes simulator shipments, demand and injected events. Jalani keeps the previous run's audit history and starts a new run.</p><button className="button danger-button" disabled={busy} onClick={() => { void action('/control/sim/reset', { confirm: true }); setReset(false); }}>Confirm reset</button><button className="button ghost" onClick={() => setReset(false)}>Keep current run</button></div>}</Panel>
    <div className="grid-two"><Panel title="Inject a domain event"><form className="control-form" onSubmit={inject}><label>Event<select value={event} onChange={e => { setEvent(e.target.value); setTarget(''); setMagnitude(e.target.value === 'supply_shortfall' ? 0.5 : e.target.value === 'shipment_delay' ? 8 : 1.8); }}>{['demand_spike', 'route_disruption', 'shipment_delay', 'supply_shortfall', 'station_outage', 'depot_constraint'].map(x => <option key={x} value={x}>{sentence(x)}</option>)}</select></label><label>Affected location<select value={target} onChange={e => setTarget(e.target.value)}><option value="">All matching locations</option>{targetItems?.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label><div className="form-row"><label>Duration (ticks)<input type="number" min="1" max="10000" required value={duration} onChange={e => setDuration(Number(e.target.value))} /></label>{['demand_spike', 'supply_shortfall', 'shipment_delay'].includes(event) && <label>{event === 'shipment_delay' ? 'Delay ticks' : 'Multiplier / factor'}<input type="number" min="0.01" max={event === 'supply_shortfall' ? 1 : 100} step="0.01" required value={magnitude} onChange={e => setMagnitude(Number(e.target.value))} /></label>}</div><p className="fineprint">Starts at tick {(state?.tick || 0) + 1}. Step or run the simulator to activate it.</p><button className="button primary" disabled={busy}>Schedule event <ArrowRight size={15} /></button></form></Panel>
      <Panel title="Exercise failure & recovery"><form className="control-form" onSubmit={e => { e.preventDefault(); void action('/control/faults', { type: fault, duration_seconds: faultDuration, parameters: fault === 'latency' ? { delay_ms: faultValue } : fault === 'error_rate' ? { rate: faultValue } : {} }); }}><label>Fault<select value={fault} onChange={e => { setFault(e.target.value); setFaultValue(e.target.value === 'error_rate' ? 0.25 : 500); }}>{['stale_data', 'unavailable', 'latency', 'error_rate', 'stream_disconnect'].map(f => <option value={f} key={f}>{sentence(f)}</option>)}</select></label><label>Duration (real seconds)<input type="number" min="1" max="3600" required value={faultDuration} onChange={e => setFaultDuration(Number(e.target.value))} /></label>{['latency', 'error_rate'].includes(fault) && <label>{fault === 'latency' ? 'Latency (ms)' : 'Failure rate (0–1)'}<input type="number" min="0" max={fault === 'latency' ? 30000 : 1} step={fault === 'latency' ? 1 : 0.01} value={faultValue} onChange={e => setFaultValue(Number(e.target.value))} /></label>}<div className="row"><button className="button" disabled={busy}>Inject fault</button><button className="button primary" type="button" disabled={busy} onClick={() => action('/control/faults/clear')}><RefreshCw size={15} />Clear all faults</button></div><p className="fineprint">Stale or unavailable data must pause execution while cached views remain readable. Faults expire automatically.</p></form></Panel></div>
    <Panel title="Decision policy"><div className="policy-controls"><label>Operation mode<select value={state?.autopilot || 'advisory'} disabled={busy} onChange={e => action('/settings/autopilot', { mode: e.target.value })}>{['manual', 'advisory', 'auto'].map(x => <option key={x}>{x}</option>)}</select></label><label>Active policy<select value={state?.policy_version || 'greedy_v1'} disabled={busy} onChange={e => action('/settings/policy', { version: e.target.value })}><option value="greedy_v1">Greedy · forecast priority</option><option value="naive_reorder">Naive · reorder below 40%</option></select></label><p>Auto mode executes eligible, high-confidence routine proposals. Crisis, cross-region and rationing decisions still require human review.</p></div></Panel></>;
}
