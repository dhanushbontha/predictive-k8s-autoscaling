import http from 'k6/http';
import { check } from 'k6';

const TARGET_URL = __ENV.TARGET_URL || 'http://workload-app.workload.svc.cluster.local:8000/work';
const RATE = parseInt(__ENV.RATE || '6', 10);
const DURATION = __ENV.DURATION || '30s';

export const options = {
  discardResponseBodies: true,
  noConnectionReuse: true,
  rps: 0,
  scenarios: {
    constant_load: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 150,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  const params = {
    timeout: '5s',
  };
  const res = http.get(TARGET_URL, params);
  check(res, {
    'status is 200': (r) => r.status === 200,
  });
}
