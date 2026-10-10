import appConfig from '../../app.config';
import eas from '../../eas.json';

// Declared locally: pulling in @types/node for `process` would change global
// setTimeout typing (App.tsx timer refs) for the whole program.
declare const process: { env: Record<string, string | undefined> };

interface ResolvedConfig {
  slug: string;
  extra: {
    beta: { enabled: boolean };
    revenueCat: { androidPublicKey: string };
  };
}

const BASE_CONFIG = {
  name: 'QR Generator',
  slug: 'qr-generator',
  extra: {
    revenueCat: { androidPublicKey: 'goog_placeholder' },
    beta: { enabled: false },
  },
};

function resolveWith(value: string | undefined): ResolvedConfig {
  const previous = process.env.BETA_BUILD;
  if (value === undefined) {
    delete process.env.BETA_BUILD;
  } else {
    process.env.BETA_BUILD = value;
  }
  try {
    return appConfig({ config: BASE_CONFIG });
  } finally {
    if (previous === undefined) {
      delete process.env.BETA_BUILD;
    } else {
      process.env.BETA_BUILD = previous;
    }
  }
}

describe('app.config.js beta flag', () => {
  it('stays false when BETA_BUILD is unset', () => {
    expect(resolveWith(undefined).extra.beta.enabled).toBe(false);
  });

  it('stays false for "0"', () => {
    expect(resolveWith('0').extra.beta.enabled).toBe(false);
  });

  it('stays false for "true" so only the exact token enables beta', () => {
    expect(resolveWith('true').extra.beta.enabled).toBe(false);
  });

  it('enables beta only for "1"', () => {
    expect(resolveWith('1').extra.beta.enabled).toBe(true);
  });

  it('preserves the rest of the config, including the RevenueCat key', () => {
    const resolved = resolveWith('1');
    expect(resolved.extra.revenueCat.androidPublicKey).toBe('goog_placeholder');
    expect(resolved.slug).toBe('qr-generator');
  });
});

describe('eas.json profiles', () => {
  it('production never sets BETA_BUILD', () => {
    const production: Record<string, unknown> = eas.build.production;
    expect(production.env).toBeUndefined();
    expect(JSON.stringify(production)).not.toContain('BETA_BUILD');
  });

  it('beta profile opts into the build with BETA_BUILD=1', () => {
    const beta: Record<string, unknown> = eas.build.beta;
    const env: Record<string, unknown> = beta.env as Record<string, unknown>;
    expect(env.BETA_BUILD).toBe('1');
  });
});
