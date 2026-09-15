export type WorkerStatus = 'idle' | 'running' | 'error';

export type WsStatus = 'connecting' | 'connected' | 'disconnected';

export interface WorkerEvent {
  event_type: string;
  agent_id: string;
  agent_name: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface WorkerState {
  name: string;
  status: WorkerStatus;
  lastEventType: string | null;
  lastEventTimestamp: string | null;
}

export interface WorkerNodeData {
  workerName: string;
  status: WorkerStatus;
  lastEventType: string | null;
  lastEventTimestamp: string | null;
  [key: string]: unknown;
}

export interface TaskRecord {
  task_id: string;
  goal: string;
  status: 'queued' | 'running' | 'done' | 'error';
  submitted_at: string;
  started_at: string | null;
  finished_at: string | null;
  result: unknown | null;
}
