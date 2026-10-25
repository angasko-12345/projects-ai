import AsyncStorage from '@react-native-async-storage/async-storage';
import * as StoreReview from 'expo-store-review';

import { markPrompted, parseReviewState, recordSuccess, shouldRequestReview } from './review';

const REVIEW_KEY = '@qrgen:v1:review';

let queue: Promise<void> = Promise.resolve();

async function noteSuccessNow(sessionHadError: boolean): Promise<void> {
  const state = recordSuccess(parseReviewState(await AsyncStorage.getItem(REVIEW_KEY)));
  const now = Date.now();
  if (shouldRequestReview(state, now, sessionHadError) && (await StoreReview.hasAction())) {
    // Persist first so a crash or quota-suppressed dialog still counts as an ask.
    await AsyncStorage.setItem(REVIEW_KEY, JSON.stringify(markPrompted(now)));
    await StoreReview.requestReview();
    return;
  }
  await AsyncStorage.setItem(REVIEW_KEY, JSON.stringify(state));
}

/**
 * Call after a successful save/share/export. Serialized so rapid taps cannot
 * race the stored counter. Never throws: a review prompt must not break exports.
 */
export function noteSuccessfulExport(sessionHadError: boolean): Promise<void> {
  queue = queue.then(() => noteSuccessNow(sessionHadError)).catch(() => undefined);
  return queue;
}
