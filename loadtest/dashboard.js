import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '20s', target: 10 },
    { duration: '40s', target: 30 },
    { duration: '20s', target: 0 },
  ],
  thresholds: {
    'http_req_duration{workload:dashboard}': ['p(95)<500'],
    'http_req_failed{workload:dashboard}': ['rate<0.01'],
    checks: ['rate>0.99'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

const base = __ENV.BASE_URL || 'http://localhost:3000';

export function setup() {
  if (!__ENV.SMOKE_USER || !__ENV.SMOKE_PASSWORD) {
    throw new Error('Set test credentials with SMOKE_USER and SMOKE_PASSWORD');
  }
  const response = http.post(`${base}/api/auth/login`, JSON.stringify({
    username: __ENV.SMOKE_USER, password: __ENV.SMOKE_PASSWORD,
  }), { headers: { 'Content-Type': 'application/json' }, tags: { workload: 'login' } });
  if (response.status !== 200 || !response.json('access_token')) {
    throw new Error('Login failed; load test was not started');
  }
  return { token: response.json('access_token') };
}

export default function (data) {
  const response = http.get(`${base}/api/network/state`, {
    headers: { Authorization: `Bearer ${data.token}` },
    tags: { workload: 'dashboard' },
  });
  check(response, {
    'network read succeeds': (r) => r.status === 200,
    'payload has discovered stations': (r) => {
      try { return Array.isArray(r.json('stations')) && r.json('stations').length > 0; }
      catch (_) { return false; }
    },
  });
  sleep(1);
}

export function handleSummary(data) {
  delete data.setup_data; // holds the bearer token; never write credentials into evidence
  return { [__ENV.OUTPUT_FILE || 'artifacts/load-dashboard.json']: JSON.stringify(data, null, 2) };
}
