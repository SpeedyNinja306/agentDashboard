import { useEffect, useRef, useState } from 'react';
import type { WorkerEvent, WorkerState, WsStatus } from '../types';

// Workers the dashboard knows about. Extend when new worker types are added.
const KNOWN_WORKERS = new Set(['research-specialist']);

const DEFAULT_STATE: WorkerState = {
  name: 'research-specialist',
  status: 'idle',
  lastEventType: null,
  lastEventTimestamp: null,
};

function initialWorkerMap(): Record<string, WorkerState> {
  return { 'research-specialist': { ...DEFAULT_STATE } };
}

function applyEvent(
  prev: Record<string, WorkerState>,
  event: WorkerEvent,
): Record<string, WorkerState> {
  const { event_type, agent_name, timestamp } = event;
  if (!KNOWN_WORKERS.has(agent_name)) return prev;

  const current = prev[agent_name] ?? { name: agent_name, status: 'idle', lastEventType: null, lastEventTimestamp: null };

  let { status } = current;
  if (event_type === 'worker_spawned') status = 'running';
  else if (event_type === 'completed') status = 'idle';
  else if (event_type === 'error' && agent_name !== 'orchestrator') status = 'error';

  return {
    ...prev,
    [agent_name]: {
      ...current,
      status,
      lastEventType: event_type,
      lastEventTimestamp: timestamp,
    },
  };
}

export function useWorkerEvents(url: string): {
  workers: Record<string, WorkerState>;
  wsStatus: WsStatus;
} {
  const [workers, setWorkers] = useState<Record<string, WorkerState>>(initialWorkerMap);
  const [wsStatus, setWsStatus] = useState<WsStatus>('connecting');
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let alive = true;
    let ws: WebSocket | null = null;

    function clearReconnect() {
      if (reconnectTimer.current !== null) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
    }

    function connect() {
      if (!alive) return;
      clearReconnect();
      setWsStatus('connecting');

      try {
        ws = new WebSocket(url);
      } catch {
        scheduleReconnect();
        return;
      }

      ws.onopen = () => {
        if (!alive) { ws?.close(); return; }
        setWsStatus('connected');
      };

      ws.onmessage = (evt: MessageEvent<string>) => {
        if (!alive) return;
        let event: WorkerEvent;
        try {
          event = JSON.parse(evt.data) as WorkerEvent;
        } catch {
          return;
        }
        setWorkers(prev => applyEvent(prev, event));
      };

      ws.onclose = () => {
        if (!alive) return;
        setWsStatus('disconnected');
        scheduleReconnect();
      };

      ws.onerror = () => {
        ws?.close();
      };
    }

    function scheduleReconnect() {
      if (!alive) return;
      reconnectTimer.current = setTimeout(connect, 3000);
    }

    connect();

    return () => {
      alive = false;
      clearReconnect();
      ws?.close();
    };
  }, [url]);

  return { workers, wsStatus };
}
