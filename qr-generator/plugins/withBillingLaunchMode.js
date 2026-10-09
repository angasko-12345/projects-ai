const { withAndroidManifest } = require('@expo/config-plugins');

/**
 * Purchase flows can hand off to a banking app for payment verification.
 * RevenueCat requires the purchase-triggering activity to use launchMode
 * `standard` or `singleTop`: with the Expo/RN default `singleTask`, backgrounding
 * the app for that hand-off can cancel the purchase. Applied at prebuild time
 * so android/ stays generated and is never edited by hand.
 */
module.exports = function withBillingLaunchMode(config) {
  return withAndroidManifest(config, (modConfig) => {
    const application = modConfig.modResults.manifest.application?.[0];
    const mainActivity = application?.activity?.find(
      (activity) => activity.$['android:name'] === '.MainActivity',
    );
    if (mainActivity) {
      mainActivity.$['android:launchMode'] = 'singleTop';
    }
    return modConfig;
  });
};
