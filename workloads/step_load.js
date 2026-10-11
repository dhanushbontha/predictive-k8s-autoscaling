import http from 'k6/http';
import { check } from 'k6';

const TARGET_URL = __ENV.TARGET_URL || 'http://127.0.0.1:8080/work';
const BASE_RATE = parseInt(__ENV.BASE_RATE || '5', 10);
const PEAK_RATE = parseInt(__ENV.PEAK_RATE || '35', 10);

export const options = {
  discardResponseBodies: true,
  scenarios: {
    step_load: {
      executor: 'ramping-arrival-rate',
      startRate: BASE_RATE,
      timeUnit: '1s',
      preAllocatedVUs: 30,
      maxVUs: 200,
      stages: [
        { target: BASE_RATE, duration: '40s' },   // Stage 0: baseline warm-up (t=0 to 40)
        { target: PEAK_RATE, duration: '10s' },   // Stage 1: step up (t=40 to 50, t_onset=40)
        { target: PEAK_RATE, duration: '120s' },  // Stage 2: sustained peak (t=50 to 170)
        { target: BASE_RATE, duration: '10s' },   // Stage 3: step down (t=170 to 180)
        { target: BASE_RATE, duration: '30s' },   // Stage 4: recovery (t=180 to 210)
      ],
    },
  },
};

export default function () {
  const res = http.get(TARGET_URL);
  check(res, {
    'status is 200': (r) => r.status === 200,
  });
}
