import { useRef, useState } from 'react';
import { ORCH_HTTP_BASE } from '../config';
import type { TaskRecord } from '../types';

const API_BASE = ORCH_HTTP_BASE;

interface TaskModalProps {
  workerName: string;
  onClose: () => void;
  onSubmitted: (record: TaskRecord) => void;
}

export function TaskModal({ workerName, onClose, onSubmitted }: TaskModalProps) {
  const [goal, setGoal] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = goal.trim();
    if (!trimmed) return;

    setSubmitting(true);
    setError(null);
    try {
      const resp = await fetch(`${API_BASE}/workers/${encodeURIComponent(workerName)}/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal: trimmed }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail));
      }
      const record: TaskRecord = await resp.json();
      onSubmitted(record);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  function handleBackdropClick(e: React.MouseEvent) {
    if (e.target === e.currentTarget) onClose();
  }

  return (
    <div
      onClick={handleBackdropClick}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        style={{
          width: 480,
          maxWidth: '95vw',
          background: '#0f172a',
          border: '1px solid #334155',
          borderRadius: 10,
          padding: 24,
          fontFamily: 'ui-monospace, monospace',
          color: '#e2e8f0',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
          <h2
            style={{
              margin: 0,
              fontSize: 16,
              fontWeight: 600,
              color: '#f1f5f9',
              flex: 1,
            }}
          >
            Assign task to{' '}
            <span style={{ color: '#60a5fa' }}>{workerName}</span>
          </h2>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: '#64748b',
              cursor: 'pointer',
              fontSize: 18,
              padding: '0 4px',
              lineHeight: 1,
            }}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <label
            htmlFor="goal-input"
            style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 6 }}
          >
            Goal
          </label>
          <textarea
            id="goal-input"
            ref={textareaRef}
            value={goal}
            onChange={e => setGoal(e.target.value)}
            rows={4}
            placeholder="Describe what the worker should research…"
            disabled={submitting}
            style={{
              width: '100%',
              boxSizing: 'border-box',
              background: '#1e293b',
              border: '1px solid #334155',
              borderRadius: 6,
              color: '#e2e8f0',
              fontFamily: 'inherit',
              fontSize: 13,
              padding: '8px 10px',
              resize: 'vertical',
              outline: 'none',
            }}
          />

          {error && (
            <div
              style={{
                marginTop: 8,
                padding: '6px 10px',
                borderRadius: 6,
                background: '#7f1d1d44',
                border: '1px solid #f87171',
                color: '#fca5a5',
                fontSize: 12,
              }}
            >
              {error}
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 16 }}>
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              style={{
                padding: '7px 16px',
                borderRadius: 6,
                background: 'none',
                border: '1px solid #334155',
                color: '#94a3b8',
                cursor: 'pointer',
                fontFamily: 'inherit',
                fontSize: 13,
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !goal.trim()}
              style={{
                padding: '7px 20px',
                borderRadius: 6,
                background: submitting || !goal.trim() ? '#1e3a5f' : '#2563eb',
                border: 'none',
                color: submitting || !goal.trim() ? '#475569' : '#e2e8f0',
                cursor: submitting || !goal.trim() ? 'not-allowed' : 'pointer',
                fontFamily: 'inherit',
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              {submitting ? 'Submitting…' : 'Submit'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
