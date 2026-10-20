import { api } from './api';

/** v2.95.1 — run an AI job in the background and wait for its result.
 * The model can take longer than the web gateway allows (504), so the server
 * queues the work and the screen polls. onTick reports elapsed seconds. */
export async function runAiJob<T = any>(
  start: () => Promise<{ job_id: string }>,
  onTick?: (seconds: number) => void,
  maxSeconds = 900,
): Promise<T> {
  const { job_id } = await start();
  const began = Date.now();
  let wait = 1500;
  for (;;) {
    await new Promise((r) => setTimeout(r, wait));
    wait = Math.min(wait + 500, 4000);
    const secs = Math.round((Date.now() - began) / 1000);
    onTick?.(secs);
    const s = await api.aiJobStatus(job_id);
    if (s.status === 'done') return s.result as T;
    if (s.status === 'error') throw new Error(s.error || 'The AI job failed.');
    if (s.status === 'missing') throw new Error('The AI job was lost (the server may have restarted). Please try again.');
    if (secs > maxSeconds) throw new Error('The AI is taking unusually long. Please try again later.');
  }
}
