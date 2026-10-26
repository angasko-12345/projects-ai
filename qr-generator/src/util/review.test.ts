import {
  INITIAL_REVIEW_STATE,
  REVIEW_COOLDOWN_MS,
  REVIEW_SUCCESS_THRESHOLD,
  markPrompted,
  parseReviewState,
  recordSuccess,
  shouldRequestReview,
  type ReviewState,
} from './review';

const NOW = 1_800_000_000_000;

function afterSuccesses(count: number, base: ReviewState = INITIAL_REVIEW_STATE): ReviewState {
  let state = base;
  for (let i = 0; i < count; i += 1) {
    state = recordSuccess(state);
  }
  return state;
}

describe('shouldRequestReview', () => {
  it('waits for the third success', () => {
    expect(shouldRequestReview(afterSuccesses(0), NOW, false)).toBe(false);
    expect(shouldRequestReview(afterSuccesses(REVIEW_SUCCESS_THRESHOLD - 1), NOW, false)).toBe(false);
    expect(shouldRequestReview(afterSuccesses(REVIEW_SUCCESS_THRESHOLD), NOW, false)).toBe(true);
  });

  it('never asks in a session that showed an error', () => {
    expect(shouldRequestReview(afterSuccesses(10), NOW, true)).toBe(false);
  });

  it('asks at most once per cooldown window', () => {
    const prompted = markPrompted(NOW);
    const later = afterSuccesses(REVIEW_SUCCESS_THRESHOLD, prompted);
    expect(shouldRequestReview(later, NOW + REVIEW_COOLDOWN_MS - 1, false)).toBe(false);
    expect(shouldRequestReview(later, NOW + REVIEW_COOLDOWN_MS, false)).toBe(true);
  });

  it('needs fresh successes after a prompt even once the cooldown has passed', () => {
    const prompted = markPrompted(NOW);
    expect(prompted.successCount).toBe(0);
    expect(shouldRequestReview(prompted, NOW + REVIEW_COOLDOWN_MS * 2, false)).toBe(false);
  });
});

describe('parseReviewState', () => {
  it('defaults when nothing or garbage is stored', () => {
    expect(parseReviewState(null)).toEqual(INITIAL_REVIEW_STATE);
    expect(parseReviewState('{not json')).toEqual(INITIAL_REVIEW_STATE);
    expect(parseReviewState('42')).toEqual(INITIAL_REVIEW_STATE);
  });

  it('round-trips a stored state', () => {
    const state: ReviewState = { successCount: 2, lastPromptAt: NOW };
    expect(parseReviewState(JSON.stringify(state))).toEqual(state);
  });

  it('sanitizes bad field values', () => {
    expect(parseReviewState(JSON.stringify({ successCount: -3, lastPromptAt: 'x' }))).toEqual(
      INITIAL_REVIEW_STATE,
    );
  });
});
