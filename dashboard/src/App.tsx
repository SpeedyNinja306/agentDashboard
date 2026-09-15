import { useCallback, useEffect, useState } from 'react';
import {
  Background,
  Controls,
  ReactFlow,
  useNodesState,
  type NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { TaskModal } from './components/TaskModal';
import { ORCH_WS_URL } from './config';
import { useWorkerEvents } from './hooks/useWorkerEvents';
import { WorkerNode } from './nodes/WorkerNode';
import type { TaskRecord, WorkerNodeData } from './types';
import type { WorkerNodeType } from './nodes/WorkerNode';

const WS_URL = ORCH_WS_URL;

const nodeTypes = { worker: WorkerNode };

const INITIAL_NODES: WorkerNodeType[] = [
  {
    id: 'research-specialist',
    type: 'worker',
    position: { x: 120, y: 180 },
    data: {
      workerName: 'research-specialist',
      status: 'idle',
      lastEventType: null,
      lastEventTimestamp: null,
    },
  },
  {
    id: 'coding-agent',
    type: 'worker',
    position: { x: 460, y: 180 },
    data: {
      workerName: 'coding-agent',
      status: 'idle',
      lastEventType: null,
      lastEventTimestamp: null,
    },
  },
];

const WS_STATUS_COLOR: Record<string, string> = {
  connected: '#4ade80',
  connecting: '#facc15',
  disconnected: '#f87171',
};

export default function App() {
  const { workers, wsStatus } = useWorkerEvents(WS_URL);
  const [nodes, setNodes, onNodesChange] = useNodesState<WorkerNodeType>(INITIAL_NODES);
  const [selectedWorker, setSelectedWorker] = useState<string | null>(null);
  const [lastSubmitted, setLastSubmitted] = useState<TaskRecord | null>(null);

  // Sync worker state into node data whenever the hook emits an update.
  useEffect(() => {
    setNodes(nds =>
      nds.map(node => {
        const workerState = workers[node.data.workerName];
        if (!workerState) return node;
        const nextData: WorkerNodeData = {
          ...node.data,
          status: workerState.status,
          lastEventType: workerState.lastEventType,
          lastEventTimestamp: workerState.lastEventTimestamp,
        };
        return { ...node, data: nextData };
      }),
    );
  }, [workers, setNodes]);

  const handleNodeClick: NodeMouseHandler<WorkerNodeType> = useCallback((_evt, node) => {
    setSelectedWorker(node.data.workerName);
  }, []);

  function handleSubmitted(record: TaskRecord) {
    setLastSubmitted(record);
  }

  return (
    <div
      style={{
        width: '100vw',
        height: '100vh',
        background: '#020617',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Top bar */}
      <header
        style={{
          height: 48,
          background: '#0f172a',
          borderBottom: '1px solid #1e293b',
          display: 'flex',
          alignItems: 'center',
          padding: '0 20px',
          gap: 16,
          flexShrink: 0,
          fontFamily: 'ui-monospace, monospace',
        }}
      >
        <span style={{ color: '#f1f5f9', fontWeight: 700, fontSize: 15, letterSpacing: '-0.02em' }}>
          AgentDashboard
        </span>
        <span style={{ color: '#334155', fontSize: 14 }}>|</span>
        <span
          style={{
            fontSize: 12,
            color: '#64748b',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: WS_STATUS_COLOR[wsStatus] ?? '#64748b',
              display: 'inline-block',
            }}
          />
          {wsStatus === 'connected'
            ? 'live'
            : wsStatus === 'connecting'
              ? 'connecting…'
              : 'disconnected — retrying'}
        </span>

        {lastSubmitted && (
          <span
            style={{
              marginLeft: 'auto',
              fontSize: 12,
              color: '#94a3b8',
              background: '#1e293b',
              border: '1px solid #334155',
              borderRadius: 6,
              padding: '2px 10px',
            }}
          >
            task {lastSubmitted.task_id.slice(0, 8)} queued
          </span>
        )}
      </header>

      {/* Flow canvas */}
      <div style={{ flex: 1 }}>
        <ReactFlow
          nodes={nodes}
          edges={[]}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onNodeClick={handleNodeClick}
          fitView
          colorMode="dark"
          proOptions={{ hideAttribution: false }}
        >
          <Background color="#1e293b" gap={32} />
          <Controls />
        </ReactFlow>
      </div>

      {selectedWorker && (
        <TaskModal
          workerName={selectedWorker}
          onClose={() => setSelectedWorker(null)}
          onSubmitted={handleSubmitted}
        />
      )}
    </div>
  );
}
