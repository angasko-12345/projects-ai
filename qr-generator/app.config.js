/**
 * Beta builds are opt-in: extra.beta.enabled is true only when the build
 * environment sets BETA_BUILD=1. The expo-constants Gradle task
 * (createExpoConfig) evaluates this file on every build, so the flag follows
 * the shell that runs Gradle, never the checked-in app.json default.
 */
module.exports = ({ config }) => ({
  ...config,
  extra: {
    ...config.extra,
    beta: {
      ...config.extra?.beta,
      enabled: process.env.BETA_BUILD === '1',
    },
  },
});
