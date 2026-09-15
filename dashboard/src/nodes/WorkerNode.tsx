import { Handle, Position } from '@xyflow/react';
import type { NodeProps, Node } from '@xyflow/react';
import type { WorkerNodeData, WorkerStatus } from '../types';

export type WorkerNodeType = Node<WorkerNodeData, 'worker'>;

const STATUS_COLORS: Record<WorkerStatus, string> = {
  idle: '#4ade80',
  running: '#facc15',
  error: '#f87171',
};

const STATUS_LABELS: Record<WorkerStatus, string> = {
  idle: 'idle',
  running: 'running',
  error: 'error',
};

function formatTimestamp(iso: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return iso;
  }
}

export function WorkerNode({ data, selected }: NodeProps<WorkerNodeType>) {
  const { workerName, status, lastEventType, lastEventTimestamp } = data;
  const badgeColor = STATUS_COLORS[status];

  return (
    <div
      style={{
        width: 220,
        borderRadius: 8,
        border: selected ? '2px solid #60a5fa' : '1px solid #334155',
        background: '#0f172a',
        color: '#e2e8f0',
        fontFamily: 'ui-monospace, monospace',
        fontSize: 12,
        cursor: 'pointer',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '8px 12px',
          background: '#1e293b',
          borderBottom: '1px solid #334155',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        <span style={{ flex: 1, fontWeight: 600, fontSize: 13, color: '#f1f5f9' }}>
          {workerName}
        </span>
        <span
          style={{
            padding: '2px 8px',
            borderRadius: 99,
            background: badgeColor + '22',
            color: badgeColor,
            border: `1px solid ${badgeColor}55`,
            fontSize: 11,
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
          }}
        >
          {STATUS_LABELS[status]}
        </span>
      </div>

      {/* Body */}
      <div style={{ padding: '8px 12px', lineHeight: 1.6 }}>
        <div style={{ color: '#94a3b8', fontSize: 11 }}>last event</div>
        <div style={{ color: '#cbd5e1' }}>{lastEventType ?? '—'}</div>
        <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 4 }}>at</div>
        <div style={{ color: '#cbd5e1' }}>{formatTimestamp(lastEventTimestamp)}</div>
      </div>

      <div
        style={{
          padding: '4px 12px 8px',
          color: '#475569',
          fontSize: 10,
          textAlign: 'center',
        }}
      >
        click to assign task
      </div>

      <Handle type="source" position={Position.Bottom} style={{ background: '#475569' }} />
      <Handle type="target" position={Position.Top} style={{ background: '#475569' }} />
    </div>
  );
}
