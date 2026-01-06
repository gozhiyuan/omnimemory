import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const VUS = __ENV.VUS ? parseInt(__ENV.VUS, 10) : 5;
const DURATION = __ENV.DURATION || '1m';

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    http_req_duration: ['p(95)<3000'],
  },
};

export default function () {
  const payload = JSON.stringify({
    message: 'What did I do yesterday?',
    session_id: 'loadtest-session',
    tz_offset_minutes: 0,
  });

  const res = http.post(`${BASE_URL}/chat`, payload, {
    headers: { 'Content-Type': 'application/json' },
  });

  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  sleep(1);
}
