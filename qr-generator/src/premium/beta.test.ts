import {
  betaPurchaseDisabledOutcome,
  isBetaBuildEnabled,
  PURCHASES_DISABLED_MESSAGE,
} from './beta';

function withExtra(extra: unknown) {
  return extra === undefined ? null : { extra };
}

describe('isBetaBuildEnabled', () => {
  it('is off when the embedded config has no extra', () => {
    expect(isBetaBuildEnabled(withExtra(undefined))).toBe(false);
  });

  it('is off when no beta key is configured', () => {
    expect(isBetaBuildEnabled(withExtra({ revenueCat: { androidPublicKey: '' } }))).toBe(false);
  });

  it('is on only when beta.enabled is explicitly true', () => {
    expect(isBetaBuildEnabled(withExtra({ beta: { enabled: true } }))).toBe(true);
  });

  it('is off when beta.enabled is false', () => {
    expect(isBetaBuildEnabled(withExtra({ beta: { enabled: false } }))).toBe(false);
  });

  it('is off for truthy non-boolean values so a typo cannot enable beta silently', () => {
    expect(isBetaBuildEnabled(withExtra({ beta: { enabled: 'true' } }))).toBe(false);
    expect(isBetaBuildEnabled(withExtra({ beta: { enabled: 1 } }))).toBe(false);
  });

  it('is off when the beta section is missing or malformed', () => {
    expect(isBetaBuildEnabled(withExtra({ beta: {} }))).toBe(false);
    expect(isBetaBuildEnabled(withExtra({ beta: null }))).toBe(false);
    expect(isBetaBuildEnabled(withExtra({ beta: 'enabled' }))).toBe(false);
    expect(isBetaBuildEnabled(null)).toBe(false);
  });
});

describe('betaPurchaseDisabledOutcome', () => {
  it('refuses the purchase instead of reporting a fake success', () => {
    const outcome = betaPurchaseDisabledOutcome();
    expect(outcome.status).toBe('unavailable');
    expect(outcome.status).not.toBe('success');
    expect(outcome.status === 'unavailable' ? outcome.message : null).toBe(
      PURCHASES_DISABLED_MESSAGE,
    );
  });
});
