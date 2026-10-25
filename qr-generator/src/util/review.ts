/**
 * Pure in-app review timing rules. No native imports here so the rules stay
 * unit-testable; `reviewPrompt.ts` wires them to storage and expo-store-review.
 *
 * Rules (Google Play in-app review guidance: ask after a success moment,
 * never from a button, never spam):
 * - only after the REVIEW_SUCCESS_THRESHOLD-th successful save/share/export
 *   since the last prompt,
 * - never in a session that showed an error,
 * - at most once per REVIEW_COOLDOWN_MS.
 */

export const REVIEW_SUCCESS_THRESHOLD = 3;
export const REVIEW_COOLDOWN_MS = 90 * 24 * 60 * 60 * 1000;

export interface ReviewState {
  /** Successful saves/shares/exports since the last prompt. */
  successCount: number;
  /** Epoch ms of the last prompt, or null if the app never asked. */
  lastPromptAt: number | null;
}

export const INITIAL_REVIEW_STATE: ReviewState = { successCount: 0, lastPromptAt: null };

export function recordSuccess(state: ReviewState): ReviewState {
  return { ...state, successCount: state.successCount + 1 };
}

export function shouldRequestReview(
  state: ReviewState,
  now: number,
  sessionHadError: boolean,
): boolean {
  if (sessionHadError) {
    return false;
  }
  if (state.successCount < REVIEW_SUCCESS_THRESHOLD) {
    return false;
  }
  if (state.lastPromptAt !== null && now - state.lastPromptAt < REVIEW_COOLDOWN_MS) {
    return false;
  }
  return true;
}

/** After asking, start counting again so the next ask needs fresh successes too. */
export function markPrompted(now: number): ReviewState {
  return { successCount: 0, lastPromptAt: now };
}

/** Tolerates missing, corrupt, or older/newer stored shapes. */
export function parseReviewState(raw: string | null): ReviewState {
  if (!raw) {
    return INITIAL_REVIEW_STATE;
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') {
      return INITIAL_REVIEW_STATE;
    }
    const { successCount, lastPromptAt } = parsed as Record<string, unknown>;
    return {
      successCount:
        typeof successCount === 'number' && Number.isFinite(successCount) && successCount > 0
          ? Math.floor(successCount)
          : 0,
      lastPromptAt:
        typeof lastPromptAt === 'number' && Number.isFinite(lastPromptAt) ? lastPromptAt : null,
    };
  } catch {
    return INITIAL_REVIEW_STATE;
  }
}
