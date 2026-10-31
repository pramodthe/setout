export const MAX_ATTEMPTS = 4;

/** The line shown while a job runs. Attempt 2 and later are repairs. */
export function attemptLabel(attempt: number | null, state: string | null): string | null {
  if (state === "reconnecting") return "Reconnecting…";
  if (state === "failed") return "The build failed.";
  if (attempt == null || attempt < 1) {
    if (state === "queued" || state === "running") return "Working…";
    return null;
  }
  if (attempt > 1) return `Repairing, attempt ${attempt} of ${MAX_ATTEMPTS}`;
  return `Building, attempt ${attempt} of ${MAX_ATTEMPTS}`;
}
