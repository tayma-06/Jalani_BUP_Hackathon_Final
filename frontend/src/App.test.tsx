import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App, { RecommendationCard } from './App';
import { api, label, number, percent } from './api';
import type { Fuel, Network, Recommendation, Session } from './types';

const fuelState = (inventory: number, risk: number) => ({ inventory, capacity: 15000, in_transit: 0, forecast_12h: 4000, hours_to_stockout: risk > 0.5 ? 2.5 : null, risk, risk_level: risk > 0.5 ? 'HIGH' : 'LOW' });
const tankFuels = (inventory: number, risk: number) => ({ DIESEL: fuelState(inventory, risk), PETROL: fuelState(inventory, 0), OCTANE: fuelState(inventory, 0) }) as Record<Fuel, ReturnType<typeof fuelState>>;

function network(overrides: Partial<Network> = {}): Network {
  return {
    run_id: 'run-1', tick: 42, tick_minutes: 15, sim_time: '2026-01-01T10:30:00', sim_status: 'RUNNING', mode: 'NORMAL', stale: false,
    data_age_s: 0.4, execution_blocked: false, reason: '', stream: 'connected', autopilot: 'advisory', policy_version: 'greedy_v1', history_gap: false,
    stations: [{ id: 'station-mirpur', name: 'Mirpur Fuel Station', region_id: 'region-dhaka', status: 'OPEN', demand_multiplier: 1, fuels: tankFuels(1000, 0.9) }],
    depots: [{ id: 'depot-gazipur', name: 'Gazipur Depot', region_id: 'region-dhaka', status: 'OPEN', dispatch_used: 0, dispatch_capacity_per_tick: 12000, fuels: tankFuels(45000, 0) }],
    routes: [{ id: 'route-gazipur-mirpur', source_depot_id: 'depot-gazipur', destination_station_id: 'station-mirpur', transit_ticks: 2, max_shipment: 7000, status: 'AVAILABLE' }],
    regions: [{ id: 'region-dhaka', name: 'Dhaka Division', fuels: { DIESEL: { demand_last_hour: 900, normal_last_hour: 800, forecast_12h: 9000 }, PETROL: { demand_last_hour: 500, normal_last_hour: 500, forecast_12h: 5000 }, OCTANE: { demand_last_hour: 200, normal_last_hour: 200, forecast_12h: 2000 } } }],
    kpis: { service_level: 0.973, unmet_liters: 1250, in_transit_liters: 0, open_alerts: 2, pending_recommendations: 1 },
    ...overrides,
  };
}

const recommendation: Recommendation = {
  id: 'rec-1', status: 'PROPOSED', station_id: 'station-mirpur', fuel_type: 'DIESEL', current_inventory: 1000, projected_stockout_hours: 2.5, expected_demand_12h: 4000,
  action: { source_depot_id: 'depot-gazipur', route_id: 'route-gazipur-mirpur', quantity: 7000, eta_tick: 44 },
  impact: { risk_before: 0.9, risk_after: 0.05, unmet_before_l: 3000, unmet_after_l: 0 },
  confidence: { score: 0.8, level: 'HIGH' }, requires_review: true,
  signals: ['Model estimates 90% stockout risk over 12 hours.'], constraints: ['Route limit 7,000 L'], alternatives: [{ label: 'Do nothing', risk_after: 0.9 }],
  explanation: 'Send 7,000 L of diesel from Gazipur Depot to Mirpur.', explanation_source: 'template', policy_version: 'greedy_v1', model_version: 'profile_v1',
};

const health = { status: 'healthy', mode: 'NORMAL', version: '1.2.3', git_sha: 'abcdef1234567890', p95_latency_ms: 2, error_rate: 0, reason: '', components: { simulator: { status: 'healthy' } } };

type Call = { path: string; method: string; body?: unknown; auth?: string };
let calls: Call[];
let state: Network;
const reply = (status: number, data: unknown) => Promise.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));

function mockBackend(users: Record<string, [string, Session['role']]> = { operator: ['demo-operator', 'operator'] }) {
  vi.stubGlobal('fetch', vi.fn((input: string, init: RequestInit = {}) => {
    const path = input.replace(/^\/api/, '');
    const method = init.method || 'GET';
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, method, body, auth: (init.headers as Record<string, string> | undefined)?.Authorization });
    if (path === '/auth/login') {
      const user = users[body.username];
      return user && user[0] === body.password
        ? reply(200, { access_token: `token-${body.username}`, role: user[1], username: body.username })
        : reply(401, { detail: 'Invalid username or password' });
    }
    if (path === '/network/state') return reply(200, state);
    if (path === '/health') return reply(200, health);
    if (path === '/recommendations') return reply(200, [recommendation]);
    if (method === 'POST') return reply(200, { status: 'PENDING' });
    return reply(200, []);
  }));
}

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
}

async function signIn(username: string, password: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Username'), username);
  await user.type(screen.getByLabelText('Password'), password);
  await user.click(screen.getByRole('button', { name: /Enter workspace/ }));
  return user;
}

beforeEach(() => { calls = []; state = network(); sessionStorage.clear(); mockBackend(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe('formatting helpers', () => {
  it('formats litres, percentages and identifiers for operators', () => {
    expect(number(12345.6)).toBe('12,346');
    expect(number(null)).toBe('—');
    expect(percent(0.973)).toBe('97.3%');
    expect(percent(undefined)).toBe('—');
    expect(label('route_disruption')).toBe('route disruption');
  });
});

describe('api client', () => {
  it('sends the bearer token and surfaces backend error messages', async () => {
    sessionStorage.setItem('jalani-session', JSON.stringify({ access_token: 'abc', role: 'operator', username: 'operator' }));
    vi.stubGlobal('fetch', vi.fn(() => reply(409, { detail: { code: 'ROUTE_DISRUPTED', message: 'Route is disrupted' } })));
    await expect(api('/recommendations/x/approve', 'POST', {})).rejects.toThrow('Route is disrupted');
    const [, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer abc');
  });

  it('signals session expiry on 401 so the app returns to sign-in', async () => {
    const expired = vi.fn();
    window.addEventListener('session-expired', expired);
    vi.stubGlobal('fetch', vi.fn(() => reply(401, { detail: 'Token expired' })));
    await expect(api('/network/state')).rejects.toThrow('Token expired');
    expect(expired).toHaveBeenCalledOnce();
    window.removeEventListener('session-expired', expired);
  });
});

describe('sign-in', () => {
  it('rejects wrong credentials without opening the workspace', async () => {
    renderApp();
    await signIn('lala@gmail.com', 'wrong-pass');
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid username or password');
    expect(screen.queryByText('Network overview')).not.toBeInTheDocument();
    expect(sessionStorage.getItem('jalani-session')).toBeNull();
  });

  it('opens the live overview with KPIs and version after a valid sign-in', async () => {
    renderApp();
    await signIn('operator', 'demo-operator');
    expect(await screen.findByText('Network overview')).toBeInTheDocument();
    expect(await screen.findByText('97.3%')).toBeInTheDocument();
    expect(screen.getByText('Tick 42')).toBeInTheDocument();
    expect(await screen.findByText(/v1\.2\.3 · abcdef12/)).toBeInTheDocument();
    expect(calls.find(c => c.path === '/network/state')?.auth).toBe('Bearer token-operator');
  });

  it('returns to sign-in on logout and forgets the session', async () => {
    renderApp();
    const user = await signIn('operator', 'demo-operator');
    await screen.findByText('Network overview');
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(screen.getByRole('button', { name: /Enter workspace/ })).toBeInTheDocument();
    expect(sessionStorage.getItem('jalani-session')).toBeNull();
  });
});

describe('role restrictions in the UI', () => {
  it('hides the control room from operators and shows it to admins', async () => {
    mockBackend({ operator: ['demo-operator', 'operator'], admin: ['demo-admin', 'admin'] });
    const first = renderApp();
    await signIn('operator', 'demo-operator');
    const nav = await screen.findByRole('navigation', { name: 'Main navigation' });
    expect(within(nav).queryByRole('button', { name: /Control room/ })).not.toBeInTheDocument();
    first.unmount(); sessionStorage.clear();

    renderApp();
    await signIn('admin', 'demo-admin');
    const adminNav = await screen.findByRole('navigation', { name: 'Main navigation' });
    expect(within(adminNav).getByRole('button', { name: /Control room/ })).toBeInTheDocument();
  });

  it('gives viewers read-only recommendations', async () => {
    mockBackend({ viewer: ['demo-viewer', 'viewer'] });
    renderApp();
    const user = await signIn('viewer', 'demo-viewer');
    await screen.findByText('Network overview');
    await user.click(within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', { name: /Recommendations/ }));
    expect(await screen.findByText('Mirpur Fuel Station', { selector: 'h2' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Approve shipment/ })).not.toBeInTheDocument();
  });
});

describe('recommendation decisions', () => {
  const name = (id: string) => ({ 'station-mirpur': 'Mirpur Fuel Station', 'depot-gazipur': 'Gazipur Depot' }[id] || id);

  it('approves with the operator-chosen quantity', async () => {
    const action = vi.fn().mockResolvedValue(undefined);
    render(<RecommendationCard recommendation={recommendation} name={name} canAct blocked={false} action={action} />);
    const user = userEvent.setup();
    const quantity = screen.getByLabelText('Quantity for Mirpur Fuel Station DIESEL');
    await user.clear(quantity);
    await user.type(quantity, '5000');
    await user.click(screen.getByRole('button', { name: /Approve shipment/ }));
    expect(action).toHaveBeenCalledWith('/recommendations/rec-1/approve', { quantity: 5000 });
  });

  it('refuses quantities above the validated recommendation', async () => {
    render(<RecommendationCard recommendation={recommendation} name={name} canAct blocked={false} action={vi.fn()} />);
    const user = userEvent.setup();
    const quantity = screen.getByLabelText('Quantity for Mirpur Fuel Station DIESEL');
    await user.clear(quantity);
    await user.type(quantity, '9000');
    expect(screen.getByRole('button', { name: /Approve shipment/ })).toBeDisabled();
  });

  it('requires a reason before rejecting', async () => {
    const action = vi.fn().mockResolvedValue(undefined);
    render(<RecommendationCard recommendation={recommendation} name={name} canAct blocked={false} action={action} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Reject' }));
    const confirm = screen.getByRole('button', { name: 'Confirm rejection' });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText('Rejection reason'), 'Route crew unavailable');
    await user.click(confirm);
    expect(action).toHaveBeenCalledWith('/recommendations/rec-1/reject', { reason: 'Route crew unavailable' });
  });

  it('explains the recommendation and flags human review', () => {
    render(<RecommendationCard recommendation={recommendation} name={name} canAct blocked={false} action={vi.fn()} />);
    expect(screen.getByText(/Human review required/)).toBeInTheDocument();
    expect(screen.getByText('Route limit 7,000 L')).toBeInTheDocument();
    expect(screen.getByText('Send 7,000 L of diesel from Gazipur Depot to Mirpur.')).toBeInTheDocument();
  });

  it('blocks approval while execution is paused', () => {
    render(<RecommendationCard recommendation={recommendation} name={name} canAct blocked action={vi.fn()} />);
    expect(screen.getByRole('button', { name: /Approve shipment/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled();
  });

  it('posts the approval through the app and confirms it to the operator', async () => {
    renderApp();
    const user = await signIn('operator', 'demo-operator');
    await screen.findByText('Network overview');
    await user.click(within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', { name: /Recommendations/ }));
    await user.click(await screen.findByRole('button', { name: /Approve shipment/ }));
    await waitFor(() => expect(calls).toContainEqual(expect.objectContaining({ path: '/recommendations/rec-1/approve', method: 'POST', body: { quantity: 7000 } })));
    expect(await screen.findByText('Action recorded. Refreshing the network.')).toBeInTheDocument();
  });
});

describe('degraded operation', () => {
  it('keeps cached data visible, explains the outage and disables execution', async () => {
    state = network({ mode: 'DEGRADED', stale: true, execution_blocked: true, reason: 'Simulator unavailable — using cached snapshot.', data_age_s: 42 });
    renderApp();
    const user = await signIn('operator', 'demo-operator');
    expect(await screen.findByText('Simulator unavailable — using cached snapshot.')).toBeInTheDocument();
    expect(screen.getByText(/Allocation execution is paused/)).toBeInTheDocument();
    expect(screen.getByText('97.3%')).toBeInTheDocument();
    await user.click(within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', { name: /Recommendations/ }));
    expect(await screen.findByRole('button', { name: /Approve shipment/ })).toBeDisabled();
  });
});
