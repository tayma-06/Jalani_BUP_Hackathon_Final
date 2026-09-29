import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import Assistant from './Assistant';

afterEach(() => vi.unstubAllGlobals());

it('shows fallback evidence and warns when a briefing is stale', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    source: 'template', explanation: 'Recorded facts [network]', uncertainty: 'Review evidence.',
    fallback_reason: 'AI is not configured.', tick: 24, run_id: 'old', stale: true,
    sources: [{ id: 'network', title: 'Network snapshot', data: { tick: 24 } }],
  }), { status: 200 }));
  vi.stubGlobal('fetch', fetch);
  render(<QueryClientProvider client={new QueryClient()}><Assistant /></QueryClientProvider>);
  await userEvent.click(screen.getByRole('button', { name: 'Generate briefing' }));
  expect(await screen.findByText('Recorded facts [network]')).toBeInTheDocument();
  expect(screen.getByText('AI is not configured.')).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('older or unavailable');
  expect(screen.getByText('[network] Network snapshot')).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
});
