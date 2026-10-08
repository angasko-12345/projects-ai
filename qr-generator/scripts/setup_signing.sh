#!/usr/bin/env bash
# Local release signing for QR Generator.
#
# Generates android/app/release-qr.jks + android/keystore.properties and wires
# them into android/app/build.gradle so `./gradlew bundleRelease` produces a
# Play-uploadable, locally-signed .aab. Everything it writes lives under
# android/, which is gitignored, so no secrets ever enter version control.
#
# EAS Build does not need this script: it signs with credentials from your
# EAS account (`eas credentials` or the prompt during `eas build`).
#
# Usage:
#   npx expo prebuild -p android --no-install   # once, creates android/
#   ./scripts/setup_signing.sh                  # then run this
#
# Non-interactive passwords (e.g. CI): set SIGNING_STORE_PASS and
# SIGNING_KEY_PASS before running.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$SCRIPT_DIR/.."

APP_DIR="android/app"
STORE_FILE="$APP_DIR/release-qr.jks"
PROPS_FILE="android/keystore.properties"
GRADLE_FILE="$APP_DIR/build.gradle"

if [ ! -f "android/gradlew" ]; then
  echo "android/ not found. Run: npx expo prebuild -p android --no-install" >&2
  exit 1
fi

# keytool ships with a JDK; interactive shells often expose only a JRE shim
# directory, and non-interactive shells can lack the Windows PATH entirely, so
# check JAVA_HOME, PATH, then the standard install locations.
find_keytool() {
  if [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/keytool" ]; then
    echo "$JAVA_HOME/bin/keytool"
    return 0
  fi
  if command -v keytool >/dev/null 2>&1; then
    command -v keytool
    return 0
  fi
  local dir
  for dir in "C:/Program Files/Java"/jdk-* "C:/Program Files"/graalvm*/*; do
    if [ -f "$dir/bin/keytool.exe" ]; then
      echo "$dir/bin/keytool.exe"
      return 0
    fi
  done
  return 1
}
if ! KEYTOOL="$(find_keytool)"; then
  echo "keytool not found. Install a JDK (17+) and/or set JAVA_HOME." >&2
  exit 1
fi
echo "Using keytool: $KEYTOOL"

if [ -z "${SIGNING_STORE_PASS:-}" ]; then
  read -rsp "Keystore password (min 6 chars): " SIGNING_STORE_PASS; echo
fi
if [ -z "${SIGNING_KEY_PASS:-}" ]; then
  read -rsp "Key password (Enter = same as keystore): " SIGNING_KEY_PASS; echo
fi
SIGNING_KEY_PASS="${SIGNING_KEY_PASS:-$SIGNING_STORE_PASS}"

if [ ! -f "$STORE_FILE" ]; then
  "$KEYTOOL" -genkeypair -v \
    -keystore "$STORE_FILE" \
    -alias qrgenerator \
    -keyalg RSA -keysize 2048 -validity 10000 \
    -storepass "$SIGNING_STORE_PASS" \
    -keypass "$SIGNING_KEY_PASS" \
    -dname "CN=QR Generator, OU=App, O=QR Generator, C=US"
  echo "Created $STORE_FILE"
else
  # Verify the given password matches the existing keystore before writing
  # properties that would otherwise fail late, during the gradle build.
  "$KEYTOOL" -list -keystore "$STORE_FILE" -storepass "$SIGNING_STORE_PASS" >/dev/null
  echo "Reusing existing $STORE_FILE"
fi

cat > "$PROPS_FILE" <<EOF
storeFile=release-qr.jks
storePassword=$SIGNING_STORE_PASS
keyAlias=qrgenerator
keyPassword=$SIGNING_KEY_PASS
EOF
echo "Wrote $PROPS_FILE (gitignored with android/)"

# Wire release signing into the generated build.gradle. Idempotent: the
# QRGEN-LOCAL-SIGNING markers guard against double application, and prebuild
# --clean regenerates the file, so this script can be re-run after any
# prebuild. EAS never sees this file: it prebuilds from scratch in the cloud.
node - <<'NODE'
const fs = require('fs');
const file = 'android/app/build.gradle';
let text = fs.readFileSync(file, 'utf8');
if (text.includes('QRGEN-LOCAL-SIGNING')) {
  console.log('build.gradle signing hook already present');
  process.exit(0);
}
const configsAnchor = `            keyPassword 'android'\n        }\n    }`;
if (!text.includes(configsAnchor)) {
  console.error('Anchor for signingConfigs not found; build.gradle layout changed.');
  process.exit(1);
}
const releaseConfigs = `            keyPassword 'android'\n        }\n        // QRGEN-LOCAL-SIGNING-START\n        release {\n            def keystorePropertiesFile = rootProject.file('keystore.properties')\n            if (!keystorePropertiesFile.exists()) {\n                throw new GradleException(\n                    'android/keystore.properties missing. For local release builds run ' +\n                    'scripts/setup_signing.sh after expo prebuild. EAS builds sign automatically.'\n                )\n            }\n            def props = new Properties()\n            keystorePropertiesFile.withInputStream { props.load(it) }\n            storeFile file(props['storeFile'])\n            storePassword props['storePassword']\n            keyAlias props['keyAlias']\n            keyPassword props['keyPassword']\n        }\n        // QRGEN-LOCAL-SIGNING-END\n    }`;
text = text.replace(configsAnchor, releaseConfigs);
const buildTypeAnchor = `        release {\n            // Caution! In production, you need to generate your own keystore file.\n            // see https://reactnative.dev/docs/signed-apk-android.\n            signingConfig signingConfigs.debug`;
if (!text.includes(buildTypeAnchor)) {
  console.error('Anchor for buildTypes.release not found; build.gradle layout changed.');
  process.exit(1);
}
text = text.replace(
  buildTypeAnchor,
  `        release {\n            // Signed with android/keystore.properties (QRGEN-LOCAL-SIGNING).\n            signingConfig signingConfigs.release`
);
fs.writeFileSync(file, text);
console.log('Patched android/app/build.gradle with local release signing');
NODE

echo
echo "Done. Next:"
echo "  cd android && ./gradlew bundleRelease"
echo "  -> android/app/build/outputs/bundle/release/app-release.aab"
