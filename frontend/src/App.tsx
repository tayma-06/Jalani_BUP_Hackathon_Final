import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, ArrowDownToLine, ArrowRight, Bell, Check, ChevronRight, CircleHelp, Clock3, Droplets, Fuel, Gauge, History, LayoutDashboard, LogOut, MapPin, Network as NetworkIcon, Play, RefreshCw, Settings2, ShieldCheck, Truck, Waves, X } from 'lucide-react';
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api, label, number, percent, readSession } from './api';
import Assistant from './Assistant';
import { fuels, type Alert, type Decision, type Forecast, type Health, type Incident, type Network, type Page, type Recommendation, type Session, type SimEvent, type Supply, type Tank, type Fuel as FuelType } from './types';

const pages: { name: Page; icon: typeof Activity }[] = [
  { name: 'Assistant', icon: CircleHelp },
  { name: 'Overview', icon: LayoutDashboard }, { name: 'Stations & depots', icon: NetworkIcon },
  { name: 'Recommendations', icon: Truck }, { name: 'Alerts', icon: Bell },
  { name: 'Supply & disruptions', icon: Waves }, { name: 'Forecasts', icon: Activity },
  { name: 'Decision history', icon: History }, { name: 'System health', icon: ShieldCheck }, { name: 'Control room', icon: Settings2 },
];
const descriptions: Record<Page, string> = {
  Assistant: 'Understand the evidence behind incidents and recommendations.',
  Overview: 'A clear view of the network. A better next decision.',
  'Stations & depots': 'Storage, demand and delivery commitments across every location.',
  Recommendations: 'Inspect the forecast. Review the impact. Approve the next move.',
  Alerts: 'Find the disruptions that need attention and track their recovery.',
  'Supply & disruptions': 'Follow incoming fuel and the events affecting its journey.',
  Forecasts: 'Explore demand, uncertainty and the next 12 hours of inventory.',
  'Decision history': 'A durable record of every shipment, operator and outcome.',
  'System health': 'Know when the platform is ready to make a decision.',
  'Control room': 'Run repeatable scenarios in the simulated environment.',
};

function useData<T>(path: string, enabled = true) {
  return useQuery<T>({ queryKey: [path], queryFn: () => api<T>(path), enabled, refetchInterval: 2000 });
}
export function Badge({ value }: { value: string }) {
  const good = ['NORMAL', 'LOW', 'OPEN', 'AVAILABLE', 'ARRIVED', 'healthy', 'connected', 'RESOLVED'].includes(value);
  const bad = ['CRITICAL', 'HIGH', 'FAILED', 'DEGRADED', 'OUTAGE', 'DISRUPTED', 'down', 'unhealthy', 'UNKNOWN'].includes(value);
  return <span className={`badge ${good ? 'good' : bad ? 'bad' : 'warm'}`}><i />{label(value)}</span>;
}
function Empty({ children }: { children: ReactNode }) { return <div className="empty"><CircleHelp size={24} /><p>{children}</p></div>; }
function Panel({ title, detail, children, className = '' }: { title: string; detail?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><div className="panel-heading"><h2>{title}</h2>{detail}</div>{children}</section>;
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
  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#" onClick={e => { e.preventDefault(); setPage('Overview'); }}><span className="brand-icon"><Droplets size={26} /></span><span>jalani<span className="brand-caption">FUEL INTELLIGENCE</span></span></a>
      <div className="workspace-label">OPERATIONS WORKSPACE</div>
      <nav aria-label="Main navigation">{pages.filter(p => p.name !== 'Control room' || session.role === 'admin').map(p => <button key={p.name} className={page === p.name ? 'nav-item active' : 'nav-item'} onClick={() => setPage(p.name)}><p.icon size={18} /><span>{p.name}</span>{p.name === 'Alerts' && !!state?.kpis.open_alerts && <b>{state.kpis.open_alerts}</b>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="simulation-note"><ShieldCheck size={20} /><div><strong>Simulation workspace</strong><span>Connected to the BUP fuel world.<br />No real infrastructure.</span></div></div>
        <div className="user"><span className="avatar">{session.username[0].toUpperCase()}</span><div><strong>{session.username}</strong><small>{session.role}</small></div><button className="icon-button" aria-label="Sign out" onClick={logout}><LogOut size={17} /></button></div>
      </div>
    </aside>
    <main>
      <header className="topbar"><div className="breadcrumb">Workspace <ChevronRight size={14} /><strong>{page}</strong></div><div className="topbar-right"><span className="sim-label">SIMULATED ENVIRONMENT</span><span className="clock"><Clock3 size={15} />Tick {number(state?.tick)}</span><Badge value={state?.sim_status || 'CONNECTING'} /></div></header>
      {(stateQuery.isError || state?.mode !== 'NORMAL' || state?.stale) && <div className="status-banner" role="status"><Activity size={18} /><div><strong>{stateQuery.isError ? 'Connection interrupted — showing the last available data.' : state?.reason || 'Connecting to the simulator.'}</strong><span>Allocation execution is paused. Data age: {number(state?.data_age_s, 1)} seconds.</span></div></div>}
      <div className="page-content"><div className="page-heading"><div><div className="eyebrow">FUEL OPERATIONS / {String(pages.findIndex(x => x.name === page) + 1).padStart(2, '0')}</div><h1>{page === 'Overview' ? 'Network overview' : page}</h1><p>{descriptions[page]}</p></div><div className="heading-side"><Badge value={state?.mode || 'CONNECTING'} /><small>{state?.sim_time ? new Date(state.sim_time).toLocaleString('en-GB', { timeZone: 'UTC', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) + ' · sim UTC' : 'Awaiting simulator clock'}</small><span className="live-dot">{state?.stream === 'connected' ? 'Live stream + REST' : 'REST polling'} · {state?.autopilot || 'advisory'}</span></div></div>
        {notice && <div className="notice" role="status"><span>{notice}</span><button aria-label="Dismiss notification" onClick={() => setNotice('')}><X size={16} /></button></div>}
        {!state?.stations.length && page !== 'System health' && page !== 'Control room' && page !== 'Assistant' ? <Panel title="Waiting for the fuel network"><Empty>Start the official simulator and check System health. The dashboard will populate after the first valid REST refresh.</Empty><button className="button" onClick={() => setPage('System health')}>View system health <ArrowRight size={16} /></button></Panel> : <>
          {page === 'Overview' && state && <Overview state={state} name={name} navigate={setPage} />}
          {page === 'Stations & depots' && state && <Stations state={state} />}
          {page === 'Recommendations' && <Recommendations state={state} name={name} canAct={canAct} blocked={blocked} action={action} />}
          {page === 'Alerts' && <Alerts canAct={canAct} busy={busy} action={action} />}
          {page === 'Supply & disruptions' && <SupplyPage state={state} name={name} />}
          {page === 'Forecasts' && state && <Forecasts state={state} />}
          {page === 'Decision history' && <HistoryPage name={name} canAct={canAct} blocked={blocked} busy={busy} action={action} />}
          {page === 'System health' && <HealthPage health={healthQuery.data} state={state} />}
          {page === 'Control room' && <ControlRoom state={state} busy={busy} action={action} />}
          {page === 'Assistant' && <Assistant state={state} />}
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
  return <div className="login"><div className="login-story"><div className="brand"><Droplets size={38} /> jalani</div><div><span className="eyebrow">FUEL SUPPLY INTELLIGENCE</span><h1>Keep the network<br />moving forward.</h1><p>See shortages before they happen.<br />Make every allocation count.</p><div className="login-flow"><span><Fuel /> Observe</span><ArrowRight /><span><Activity /> Predict</span><ArrowRight /><span><Truck /> Act</span></div></div><small>BUP HACKATHON FINALS · SIMULATED ENVIRONMENT</small></div><div className="login-form"><form onSubmit={submit}><span className="eyebrow">OPERATOR WORKSPACE</span><h2>Welcome to the control room.</h2><p>Sign in with your configured deployment credentials.</p><label>Username<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required /></label><label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>{error && <p className="error-text" role="alert">{error}</p>}<button className="button primary wide" disabled={loading}>{loading ? 'Signing in…' : 'Enter workspace'}<ArrowRight size={17} /></button><div className="login-hint"><ShieldCheck size={18} /> Role-based access · Audited decisions</div></form></div></div>;
}

function Overview({ state, name, navigate }: { state: Network; name: (id: string) => string; navigate: (page: Page) => void }) {
  const incidents = useData<Incident[]>('/incidents');
  const [summary, setSummary] = useState('');
  const [summaryError, setSummaryError] = useState('');
  const risks = state.stations.flatMap(s => fuels.map(f => ({ station: s, fuel: f, ...s.fuels[f] }))).sort((a, b) => (b.risk || 0) - (a.risk || 0)).slice(0, 5);
  const cards = [{ label: 'Network service level', value: percent(state.kpis.service_level), note: 'Demand successfully served', icon: Gauge, accent: true }, { label: 'Unmet demand', value: number(state.kpis.unmet_liters), note: 'Litres · cumulative this run', icon: Droplets }, { label: 'Fuel on the move', value: number(state.kpis.in_transit_liters), note: 'Litres · pending and in transit', icon: Truck }, { label: 'Awaiting review', value: number(state.kpis.pending_recommendations), note: `${number(state.kpis.open_alerts)} open operational alerts`, icon: Bell }];
  return <><div className="kpi-grid">{cards.map(c => <div key={c.label} className={`kpi ${c.accent ? 'accent' : ''}`}><div><span>{c.label}</span><c.icon size={19} /></div><strong>{c.value}</strong><small>{c.note}</small></div>)}</div>
    <div className="grid-main"><Panel title="The fuel network" detail={<span className="subtle">{state.depots.length} depots · {state.stations.length} stations</span>}><div className="network-legend"><span><i className="line green" />Available route</span><span><i className="line red" />Disrupted</span><span><i className="line dashed" />Cross-region</span></div><NetworkMap state={state} /><div className="panel-bottom"><MapPin size={15} />Schematic view · routes discovered from the simulator<button className="text-button" onClick={() => navigate('Stations & depots')}>Explore network <ArrowRight size={14} /></button></div></Panel>
      <Panel title="Priority watch" detail={<span className="pill">Next 12 hours</span>}><div className="risk-list">{risks.map((r, i) => <div className="risk-row" key={r.station.id + r.fuel}><span className="rank">{String(i + 1).padStart(2, '0')}</span><div><strong>{name(r.station.id).replace(' Fuel Station', '')}</strong><small>{r.fuel} · {r.hours_to_stockout == null ? 'Beyond forecast horizon' : `${number(r.hours_to_stockout, 1)}h to shortage`}</small></div><span className={`risk-number ${(r.risk || 0) >= 0.5 ? 'danger' : ''}`}>{percent(r.risk)}</span></div>)}</div><div className="fineprint">Stockout estimates use 300 demand paths by default. They are model estimates, not guaranteed outcomes.</div><button className="text-button space" onClick={() => navigate('Recommendations')}>Review recommended moves <ArrowRight size={15} /></button></Panel></div>
    <div className="grid-two"><Panel title="Regional demand" detail={<span className="subtle">Observed · last simulator hour</span>}>{state.regions.map(r => <div className="region" key={r.id}><div><h3>{r.name}</h3><span>{number(fuels.reduce((sum, f) => sum + r.fuels[f].demand_last_hour, 0))} L</span></div>{fuels.map(f => <div className="region-bar" key={f}><span>{f}</span><div><i className={f.toLowerCase()} style={{ width: `${Math.min(100, r.fuels[f].demand_last_hour / Math.max(1, ...fuels.map(k => r.fuels[k].demand_last_hour)) * 100)}%` }} /></div><b>{number(r.fuels[f].demand_last_hour)}</b></div>)}</div>)}</Panel>
      <Panel title="Operations briefing" detail={<span className="pill">Grounded template</span>}><div className="briefing"><div className="briefing-icon"><Activity size={22} /></div><h3>Every signal has a next step.</h3><p>{summary || 'Get a concise summary of service level, open alerts and allocations ready for review.'}</p><button className="button" onClick={async () => { try { setSummary((await api<{ text: string }>('/summary')).text); } catch (e) { setSummaryError(String(e)); } }}>Summarize network <ArrowRight size={14} /></button>{summaryError && <p className="error-text">{summaryError}</p>}</div><div className="incident-mini">{incidents.data?.slice(0, 2).map(i => <div key={i.id}><span>Tick {i.tick}</span><p>{i.text}</p></div>)}{!incidents.data?.length && <p className="subtle">No incidents detected in the current run.</p>}</div></Panel></div>
  </>;
}

function NetworkMap({ state }: { state: Network }) {
  const h = Math.max(330, state.stations.length * 80);
  const yDepot = (i: number) => 45 + (i + 0.5) * (h - 90) / state.depots.length;
  const yStation = (i: number) => 35 + (i + 0.5) * (h - 70) / state.stations.length;
  return <svg className="network-map" viewBox={`0 0 720 ${h}`} role="img" aria-label="Fuel network showing depots, stations and route status">
    <defs><pattern id="dots" x="0" y="0" width="18" height="18" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1" fill="#d9e0d9" /></pattern></defs><rect width="720" height={h} fill="url(#dots)" />
    <text x="55" y="27" className="map-heading">STORAGE & DISPATCH</text><text x="489" y="27" className="map-heading">STATION NETWORK</text>
    {state.routes.map(r => { const di = state.depots.findIndex(d => d.id === r.source_depot_id); const si = state.stations.findIndex(s => s.id === r.destination_station_id); if (di < 0 || si < 0) return null; const cross = state.depots[di].region_id !== state.stations[si].region_id; return <g key={r.id}><path d={`M 232 ${yDepot(di)} C 350 ${yDepot(di)}, 365 ${yStation(si)}, 482 ${yStation(si)}`} fill="none" stroke={r.status === 'DISRUPTED' ? '#c46c58' : '#5b9182'} strokeWidth={cross ? 1.6 : 2.5} strokeDasharray={cross || r.status === 'DISRUPTED' ? '6 6' : ''} opacity={cross ? 0.55 : 0.9}><title>{r.id}: {r.status}, {r.transit_ticks} ticks</title></path><circle cx="232" cy={yDepot(di)} r="4" fill="#4e8978" /></g>; })}
    {state.depots.map((d, i) => <g key={d.id} transform={`translate(35, ${yDepot(i) - 30})`}><rect width="197" height="60" rx="9" fill="#133f37" /><rect x="14" y="18" width="21" height="24" rx="4" fill="none" stroke="#c5dac6" strokeWidth="2" /><path d="M 14 25 h21 M 14 35 h21" stroke="#c5dac6" /><text x="48" y="26" fill="#fff" className="map-name">{d.name}</text><text x="48" y="44" fill="#bad0c5" className="map-small">{number(fuels.reduce((s, f) => s + d.fuels[f].inventory, 0))} L in storage</text></g>)}
    {state.stations.map((s, i) => { const risk = Math.max(...fuels.map(f => s.fuels[f].risk || 0)); return <g key={s.id} transform={`translate(482, ${yStation(i) - 27})`}><rect width="205" height="54" rx="9" fill="#fff" stroke="#dbe2db" /><circle cx="20" cy="27" r="6" fill={s.status === 'OUTAGE' || risk >= 0.5 ? '#cc755e' : '#649581'} /><text x="36" y="23" className="map-name" fill="#213b32">{s.name.replace(' Fuel Station', '')}</text><text x="36" y="40" className="map-small" fill="#79857b">{number(fuels.reduce((a, f) => a + s.fuels[f].inventory, 0))} L · {percent(risk)} risk</text></g>; })}
  </svg>;
}

function Stations({ state }: { state: Network }) {
  return <><div className="section-label">RETAIL STATIONS <span>{state.stations.length} locations</span></div><div className="tank-grid">{state.stations.map(s => <TankCard key={s.id} tank={s} />)}</div><div className="section-label">SUPPLY DEPOTS <span>{state.depots.length} locations</span></div><div className="tank-grid">{state.depots.map(s => <TankCard key={s.id} tank={s} depot />)}</div></>;
}
function TankCard({ tank, depot = false }: { tank: Tank; depot?: boolean }) {
  return <Panel title={tank.name} detail={<Badge value={tank.status} />}><div className="tank-meta">{depot ? `${number(tank.dispatch_used)} / ${number(tank.dispatch_capacity_per_tick)} L dispatch committed` : `Demand multiplier ×${number(tank.demand_multiplier, 2)}`}</div>{fuels.map(f => { const v = tank.fuels[f]; return <div className="tank-fuel" key={f}><div><strong>{f}</strong><span>{number(v.inventory)} <small>/ {number(v.capacity)} L</small></span></div><div className="fuel-track"><i className={f.toLowerCase()} style={{ width: `${Math.min(100, v.inventory / v.capacity * 100)}%` }} /></div>{!depot && <div className="fuel-details"><span>{v.hours_to_stockout == null ? 'Beyond 12h horizon' : `${number(v.hours_to_stockout, 1)}h to stockout`}</span><Badge value={v.risk_level || 'UNKNOWN'} /></div>}{!!v.in_transit && <small>{number(v.in_transit)} L incoming</small>}</div>; })}</Panel>;
}

type Action = (path: string, body?: unknown) => Promise<void>;
function Recommendations({ state, name, canAct, blocked, action }: { state?: Network; name: (id: string) => string; canAct: boolean; blocked: boolean; action: Action }) {
  const query = useData<Recommendation[]>('/recommendations');
  return <><div className="info-strip"><ShieldCheck size={19} /><span>Each quantity accounts for depot stock, route limits, tank capacity and committed shipments. Impact is a model estimate.</span><span className="pill">{state?.policy_version || 'greedy_v1'}</span></div>{query.isError && <p className="error-text">Recommendations could not refresh. Cached proposals are shown.</p>}{!query.data?.length ? <Panel title="Allocation queue"><Empty>No allocations currently need review. Recommendations appear when forecast risk or low inventory makes a delivery useful.</Empty></Panel> : <div className="recommendations-grid">{query.data.map(r => <RecommendationCard key={r.id} recommendation={r} name={name} canAct={canAct} blocked={blocked} action={action} />)}</div>}</>;
}
export function RecommendationCard({ recommendation: r, name, canAct, blocked, action }: { recommendation: Recommendation; name: (id: string) => string; canAct: boolean; blocked: boolean; action: Action }) {
  const [quantity, setQuantity] = useState(r.action.quantity); const [rejecting, setRejecting] = useState(false); const [reason, setReason] = useState('');
  return <article className="panel recommendation"><div className="rec-top"><span className="fuel-tag">{r.fuel_type}</span><Badge value={r.confidence.level} /></div><h2>{name(r.station_id)}</h2><p className="subtle">From {name(r.action.source_depot_id)} · ETA tick {r.action.eta_tick}</p><div className="rec-amount">{number(r.action.quantity)}<span>litres recommended</span></div><div className="impact"><div><small>Stockout risk</small><strong>{percent(r.impact.risk_before)}<ArrowRight size={17} /><em>{percent(r.impact.risk_after)}</em></strong></div><div><small>Expected unmet fuel</small><strong>{number(r.impact.unmet_before_l)} <span>→ {number(r.impact.unmet_after_l)} L</span></strong></div></div>{r.requires_review && <div className="review-note">Human review required · consequential or uncertain decision</div>}<details><summary>Why this allocation?<ChevronRight size={16} /></summary><p>{r.explanation}</p><h4>Signals</h4><ul>{r.signals.map(x => <li key={x}>{x}</li>)}</ul><h4>Constraints checked</h4><ul>{r.constraints.map(x => <li key={x}>{x}</li>)}</ul><h4>Alternatives</h4>{r.alternatives.map(x => <div className="alternative" key={x.label}><span>{x.label}</span><b>{percent(x.risk_after)} risk</b></div>)}<small>Confidence: {percent(r.confidence.score)} heuristic · {r.model_version} · {r.explanation_source}</small></details>{canAct && <div className="rec-actions"><label>Shipment quantity (L)<input aria-label={`Quantity for ${name(r.station_id)} ${r.fuel_type}`} type="number" min="1" max={r.action.quantity} value={quantity} onChange={e => setQuantity(Number(e.target.value))} /></label><div><button className="button primary" disabled={blocked || !Number.isFinite(quantity) || quantity <= 0 || quantity > r.action.quantity} onClick={() => action(`/recommendations/${r.id}/approve`, { quantity })}><Check size={16} />Approve shipment</button><button className="button ghost" disabled={blocked} onClick={() => setRejecting(!rejecting)}>Reject</button></div>{rejecting && <form onSubmit={e => { e.preventDefault(); void action(`/recommendations/${r.id}/reject`, { reason }); }}><input aria-label="Rejection reason" placeholder="Reason for rejecting" required value={reason} onChange={e => setReason(e.target.value)} /><button className="button" disabled={blocked || !reason.trim()}>Confirm rejection</button></form>}</div>}</article>;
}

function Alerts({ canAct, busy, action }: { canAct: boolean; busy: boolean; action: Action }) {
  const query = useData<Alert[]>('/alerts'); const [status, setStatus] = useState('ALL');
  const items = (query.data || []).filter(a => status === 'ALL' || a.status === status);
  return <Panel title="Operational alerts" detail={<select aria-label="Filter alerts" value={status} onChange={e => setStatus(e.target.value)}>{['ALL', 'OPEN', 'ACKNOWLEDGED', 'RESOLVED'].map(x => <option key={x}>{x}</option>)}</select>}>{!items.length ? <Empty>No alerts match this view.</Empty> : <div className="alert-list">{items.map(a => <div className="alert-row" key={a.id}><div className={`alert-icon ${a.severity.toLowerCase()}`}><Bell size={19} /></div><div className="grow"><div className="row"><h3>{label(a.type)}</h3><Badge value={a.severity} /></div><p>{a.message}</p><small>First seen tick {a.first_tick} · Last seen tick {a.last_tick}{a.event_ids.length ? ` · Active events: ${a.event_ids.join(', ')}` : ''}</small></div><div className="alert-action"><Badge value={a.status} />{canAct && a.status === 'OPEN' && <button disabled={busy} className="text-button" onClick={() => action(`/alerts/${encodeURIComponent(a.id)}/ack`)}>Acknowledge</button>}</div></div>)}</div>}</Panel>;
}

function SupplyPage({ state, name }: { state?: Network; name: (id: string) => string }) {
  const supply = useData<Supply[]>('/supply'); const events = useData<SimEvent[]>('/events');
  const pending = supply.data?.filter(s => s.status !== 'ARRIVED') || [];
  const lastTick = pending.length ? Math.max(...pending.map(s => s.planned_tick)) : null;
  return <><div className="info-strip"><Waves size={22} /><span>{lastTick === null ? 'No further scheduled supply is visible.' : `The last scheduled supply arrives at tick ${lastTick} — ${Math.max(0, lastTick - (state?.tick || 0))} ticks from now.`} Supply is finite.</span></div><Panel title="Incoming supply"><div className="table-wrap"><table><thead><tr><th>Shipment</th><th>Destination</th><th>Fuel</th><th>Quantity</th><th>Planned tick</th><th>Status</th></tr></thead><tbody>{supply.data?.map(s => <tr key={s.id}><td>{s.id}</td><td>{name(s.depot_id)}</td><td>{s.fuel_type}</td><td>{number(s.quantity)} L</td><td>{s.planned_tick}</td><td><Badge value={s.status} /></td></tr>)}</tbody></table></div>{!supply.data?.length && <Empty>No supply records available.</Empty>}</Panel><div className="grid-two"><Panel title="Domain events">{events.data?.length ? events.data.map(e => <div className="event-row" key={e.id}><div><h3>{label(e.type)}</h3><small>Ticks {e.start_tick}–{e.end_tick}</small><pre>{JSON.stringify(e.parameters, null, 2)}</pre></div><Badge value={e.status} /></div>) : <Empty>No domain events have been injected.</Empty>}</Panel><Panel title="Route availability">{state?.routes.map(r => <div className="route-row" key={r.id}><div><strong>{name(r.source_depot_id)}<ArrowRight size={12} />{name(r.destination_station_id)}</strong><small>{r.transit_ticks} transit ticks · maximum {number(r.max_shipment)} L / shipment</small></div><Badge value={r.status} /></div>)}</Panel></div></>;
}

function Forecasts({ state }: { state: Network }) {
  const [station, setStation] = useState(state.stations[0]?.id || ''); const [fuel, setFuel] = useState<FuelType>('DIESEL');
  const query = useData<Forecast>(`/stations/${station}/forecast?fuel=${fuel}`, !!station);
  const f = query.data;
  const history = f?.history.slice(-24).map(h => ({ tick: h.tick, actual: h.demand_liters })) || [];
  const future = f?.mean.slice(0, f.inventory_path.length).map((mean, i) => ({ tick: f.start_tick + i + 1, forecast: mean, lower: Math.max(0, mean - 1.96 * f.std[i]), upper: mean + 1.96 * f.std[i], inventory: f.inventory_path[i], band: [Math.max(0, mean - 1.96 * f.std[i]), mean + 1.96 * f.std[i]] })) || [];
  return <><div className="filter-bar"><label>Station<select value={station} onChange={e => setStation(e.target.value)}>{state.stations.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label><label>Fuel<select value={fuel} onChange={e => setFuel(e.target.value as FuelType)}>{fuels.map(s => <option key={s}>{s}</option>)}</select></label><span className="pill">{f?.model_version || 'Loading model'}</span></div><Panel title="Demand forecast" detail={<span className="subtle">Litres / simulator tick</span>}>{f ? <><div className="chart-legend"><span><i className="line green" />Observed demand</span><span><i className="line dashed" />Forecast</span><span>Shaded: approximate 95% demand band</span></div><div className="chart"><ResponsiveContainer width="100%" height={290}><ComposedChart data={[...history, ...future]} margin={{ top: 15, right: 25, bottom: 10, left: 5 }}><CartesianGrid vertical={false} stroke="#e6e9e2" /><XAxis dataKey="tick" tickLine={false} axisLine={false} /><YAxis tickLine={false} axisLine={false} /><Tooltip /><Area dataKey="band" stroke="none" fill="#d2e3d5" name="Demand band" /><Line dataKey="actual" stroke="#214f41" strokeWidth={2} dot={false} name="Actual demand" /><Line dataKey="forecast" stroke="#8b934e" strokeDasharray="5 4" dot={false} name="Forecast demand" /></ComposedChart></ResponsiveContainer></div><p className="fineprint">{f.source}. {f.uncertainty_note}</p></> : <Empty>Forecast not available yet.</Empty>}</Panel><Panel title="Projected fuel remaining" detail={<Badge value={(f?.risk || 0) >= 0.5 ? 'HIGH' : 'LOW'} />}><div className="chart"><ResponsiveContainer width="100%" height={230}><ComposedChart data={future}><CartesianGrid vertical={false} stroke="#e6e9e2" /><XAxis dataKey="tick" /><YAxis /><Tooltip /><Area dataKey="inventory" stroke="#306d56" fill="#e0ebdc" name="Fuel remaining (L)" /></ComposedChart></ResponsiveContainer></div><div className="panel-bottom">Includes pending and in-transit allocations · {f?.hours_to_stockout == null ? 'No mean-path shortage within the forecast horizon' : `Mean-path shortage in ${number(f.hours_to_stockout, 1)} hours`}</div></Panel></>;
}

function HistoryPage({ name, canAct, blocked, busy, action }: { name: (id: string) => string; canAct: boolean; blocked: boolean; busy: boolean; action: Action }) {
  const query = useData<Decision[]>('/decisions'); const [error, setError] = useState('');
  async function download() {
    try { const r = await fetch('/api/decisions/export.csv', { headers: { Authorization: `Bearer ${readSession()?.access_token}` } }); if (!r.ok) throw new Error('CSV export failed'); const url = URL.createObjectURL(await r.blob()); const a = document.createElement('a'); a.href = url; a.download = 'jalani-decisions.csv'; a.click(); URL.revokeObjectURL(url); } catch (e) { setError(String(e)); }
  }
  return <Panel title="Shipment ledger" detail={<button className="button" onClick={download}><ArrowDownToLine size={15} />Export CSV</button>}>{error && <p className="error-text">{error}</p>}{!query.data?.length ? <Empty>Approved shipments will appear here with their immutable request and simulator outcome.</Empty> : <div className="decision-list">{query.data.map(d => <div className="decision" key={d.id}><div className="row"><span className="fuel-tag">{d.request.fuel_type}</span><strong>{number(d.request.quantity)} L</strong><span className="grow" /><Badge value={d.status} /></div><h3>{name(d.request.source_depot_id)} <ArrowRight size={15} /> {name(d.request.destination_station_id)}</h3><p className="subtle">{d.actor} · {new Date(d.created_at).toLocaleString()} · Simulator #{d.sim_id ?? 'unconfirmed'}</p>{d.failure_reason && <p className="error-text">{d.failure_reason}</p>}<details><summary>Audit details <ChevronRight size={15} /></summary><p>Run: {d.run_id}</p><p>Route: {d.request.route_id}</p><p className="break">Immutable key: {d.request.idempotency_key}</p></details>{canAct && d.status === 'UNKNOWN' && <button className="button" disabled={busy} onClick={() => action(`/decisions/${d.id}/retry`)}><RefreshCw size={15} />Reconcile and retry original request</button>}{canAct && d.status === 'PENDING' && <button className="button" disabled={blocked} onClick={() => action(`/allocations/${d.sim_id}/cancel`)}>Cancel pending shipment</button>}</div>)}</div>}</Panel>;
}

function HealthPage({ health, state }: { health?: Health; state?: Network }) {
  return <><div className="kpi-grid health-kpis"><div className="kpi"><span>API p95 latency</span><strong>{number(health?.p95_latency_ms, 1)} <small>ms</small></strong><small>Rolling 1,000 application requests</small></div><div className="kpi"><span>Server error rate</span><strong>{percent(health?.error_rate)}</strong><small>Rolling requests · HTTP 5xx</small></div><div className="kpi"><span>Snapshot age</span><strong>{number(state?.data_age_s, 1)} <small>s</small></strong><small>Since last validated REST refresh</small></div><div className="kpi"><span>Execution gate</span><strong className="small-value">{state?.execution_blocked ? 'Paused' : 'Ready'}</strong><small>{state?.execution_blocked ? 'Review data and dependency health' : 'Fresh state and durable audit available'}</small></div></div><Panel title="Component health"><div className="table-wrap"><table><thead><tr><th>Component</th><th>Status</th><th>Details</th></tr></thead><tbody>{Object.entries(health?.components || {}).map(([key, value]) => <tr key={key}><td className="capitalize">{label(key)}</td><td><Badge value={value.status} /></td><td>{Object.entries(value).filter(([k]) => k !== 'status').map(([k, v]) => `${label(k)}: ${v ?? 'not measured'}`).join(' · ') || 'Responding'}</td></tr>)}</tbody></table></div></Panel><div className="grid-two"><Panel title="Observability tools"><p>Use the provisioned dashboards for request latency, inventory, stockout risk and service recovery.</p><div className="row"><a className="button" href="http://localhost:3001" target="_blank" rel="noreferrer">Local Grafana <ArrowRight size={15} /></a><a className="button" href="http://localhost:9090" target="_blank" rel="noreferrer">Local Prometheus <ArrowRight size={15} /></a></div><p className="fineprint">These addresses are for a local Compose deployment. Remote deployments should forward the configured monitoring ports.</p></Panel><Panel title="Readiness & recovery"><p>Liveness means this process can respond. Readiness also requires fresh, valid simulator data, a usable database and resolved shipment outcomes.</p><p className="subtle">History completeness: {state?.history_gap ? 'A retrieval gap was detected' : 'No gap detected in the latest retrieval'}. Model probabilities require calibration against the official simulator.</p></Panel></div></>;
}

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
  return <><div className="info-strip"><Settings2 size={20} /><span>Administrator controls affect only the connected simulator. Pause and step for repeatable demonstrations.</span></div><Panel title="Simulation clock" detail={<Badge value={state?.sim_status || 'UNKNOWN'} />}><div className="control-buttons">{['run', 'pause', 'step'].map(a => <button className={`button ${a === 'run' ? 'primary' : ''}`} disabled={busy} key={a} onClick={() => action(`/control/sim/${a}`)}>{a === 'run' && <Play size={15} />}{label(a)}{a === 'step' ? ' one tick' : ''}</button>)}<button className="button danger-button" onClick={() => setReset(!reset)}>Reset simulator</button></div>{reset && <div className="confirm"><p>Reset deletes simulator shipments, demand and injected events. Jalani keeps the previous run's audit history and starts a new run.</p><button className="button danger-button" disabled={busy} onClick={() => { void action('/control/sim/reset', { confirm: true }); setReset(false); }}>Confirm reset</button><button className="button ghost" onClick={() => setReset(false)}>Keep current run</button></div>}</Panel><div className="grid-two"><Panel title="Inject a domain event"><form className="control-form" onSubmit={inject}><label>Event<select value={event} onChange={e => { setEvent(e.target.value); setTarget(''); setMagnitude(e.target.value === 'supply_shortfall' ? 0.5 : e.target.value === 'shipment_delay' ? 8 : 1.8); }}>{['demand_spike', 'route_disruption', 'shipment_delay', 'supply_shortfall', 'station_outage', 'depot_constraint'].map(x => <option key={x} value={x}>{label(x)}</option>)}</select></label><label>Affected location<select value={target} onChange={e => setTarget(e.target.value)}><option value="">All matching locations</option>{targetItems?.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label><div className="form-row"><label>Duration (ticks)<input type="number" min="1" max="10000" required value={duration} onChange={e => setDuration(Number(e.target.value))} /></label>{['demand_spike', 'supply_shortfall', 'shipment_delay'].includes(event) && <label>{event === 'shipment_delay' ? 'Delay ticks' : 'Multiplier / factor'}<input type="number" min="0.01" max={event === 'supply_shortfall' ? 1 : 100} step="0.01" required value={magnitude} onChange={e => setMagnitude(Number(e.target.value))} /></label>}</div><p className="fineprint">Starts at tick {(state?.tick || 0) + 1}. Step or run the simulator to activate it.</p><button className="button primary" disabled={busy}>Schedule event <ArrowRight size={15} /></button></form></Panel><Panel title="Exercise failure & recovery"><form className="control-form" onSubmit={e => { e.preventDefault(); void action('/control/faults', { type: fault, duration_seconds: faultDuration, parameters: fault === 'latency' ? { delay_ms: faultValue } : fault === 'error_rate' ? { rate: faultValue } : {} }); }}><label>Fault<select value={fault} onChange={e => { setFault(e.target.value); setFaultValue(e.target.value === 'error_rate' ? 0.25 : 500); }}>{['stale_data', 'unavailable', 'latency', 'error_rate', 'stream_disconnect'].map(f => <option value={f} key={f}>{label(f)}</option>)}</select></label><label>Duration (real seconds)<input type="number" min="1" max="3600" required value={faultDuration} onChange={e => setFaultDuration(Number(e.target.value))} /></label>{['latency', 'error_rate'].includes(fault) && <label>{fault === 'latency' ? 'Latency (ms)' : 'Failure rate (0–1)'}<input type="number" min="0" max={fault === 'latency' ? 30000 : 1} step={fault === 'latency' ? 1 : 0.01} value={faultValue} onChange={e => setFaultValue(Number(e.target.value))} /></label>}<div className="row"><button className="button" disabled={busy}>Inject fault</button><button className="button primary" type="button" disabled={busy} onClick={() => action('/control/faults/clear')}><RefreshCw size={15} />Clear all faults</button></div><p className="fineprint">Stale or unavailable data must pause execution while cached views remain readable. Faults expire automatically.</p></form></Panel></div><Panel title="Decision policy"><div className="policy-controls"><label>Operation mode<select value={state?.autopilot || 'advisory'} disabled={busy} onChange={e => action('/settings/autopilot', { mode: e.target.value })}>{['manual', 'advisory', 'auto'].map(x => <option key={x}>{x}</option>)}</select></label><label>Active policy<select value={state?.policy_version || 'greedy_v1'} disabled={busy} onChange={e => action('/settings/policy', { version: e.target.value })}><option value="greedy_v1">Greedy · forecast priority</option><option value="naive_reorder">Naive · reorder below 40%</option></select></label><p>Auto mode executes eligible, high-confidence routine proposals. Crisis, cross-region and rationing decisions still require human review.</p></div></Panel></>;
}
