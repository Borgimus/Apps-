/**
 * Next.js boot hook: mark runs that were mid-flight when the previous process
 * died as "interrupted" so the user can see and resume them.
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const { recoverInterruptedRuns } = await import('./lib/orchestrator/engine');
    const n = await recoverInterruptedRuns().catch((err) => {
      console.error('[boot] run recovery failed', err);
      return 0;
    });
    if (n > 0) console.log(`[boot] marked ${n} orphaned run(s) as interrupted`);
  }
}
