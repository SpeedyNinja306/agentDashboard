// Where the dashboard reaches the orchestrator. Defaults to the loopback dev address so nothing
// changes for a normal `npm run dev`; override with VITE_ORCH_HTTP / VITE_ORCH_WS (e.g. to point
// at a server on a non-default port) without touching source.
const env = import.meta.env as unknown as Record<string, string | undefined>;

const HTTP_BASE = env.VITE_ORCH_HTTP ?? 'http://localhost:8000';
const WS_BASE = env.VITE_ORCH_WS ?? 'ws://localhost:8000';

export const ORCH_HTTP_BASE = HTTP_BASE;
export const ORCH_WS_URL = `${WS_BASE}/events?replay=50`;
