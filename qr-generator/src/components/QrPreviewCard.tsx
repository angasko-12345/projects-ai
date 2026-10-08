import { Ionicons } from '@expo/vector-icons';
import React, { useMemo } from 'react';
import { Image, StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import Svg, { Path, Rect } from 'react-native-svg';

import type { QrMatrix } from '../qr/matrix';
import { buildShapePaths } from '../qr/shape';
import { useTheme } from '../theme/ThemeContext';
import { RADIUS, SPACING, TYPE } from '../theme/tokens';
import type { QrAppearance } from '../types';

export type PreviewState =
  | { kind: 'empty' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; matrix: QrMatrix };

interface QrPreviewCardProps {
  preview: PreviewState;
  appearance: QrAppearance;
  captureViewRef: React.RefObject<View | null>;
}

export function QrPreviewCard({ preview, appearance, captureViewRef }: QrPreviewCardProps) {
  const { colors } = useTheme();
  const { width } = useWindowDimensions();
  const qrSize = Math.max(200, Math.min(width - 96, 360));

  const paths = useMemo(
    () =>
      preview.kind === 'ready'
        ? buildShapePaths(preview.matrix, appearance.style)
        : null,
    [preview, appearance.style],
  );

  return (
    <View style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}>
      {preview.kind === 'ready' && paths ? (
        <View
          ref={captureViewRef}
          collapsable={false}
          accessible
          accessibilityRole="image"
          accessibilityLabel="QR code preview"
          style={[styles.qrBox, { width: qrSize, height: qrSize, backgroundColor: appearance.background }]}
        >
          <Svg
            width={qrSize}
            height={qrSize}
            viewBox={`-4 -4 ${preview.matrix.size + 8} ${preview.matrix.size + 8}`}
          >
            <Rect
              x={-4}
              y={-4}
              width={preview.matrix.size + 8}
              height={preview.matrix.size + 8}
              fill={appearance.background}
            />
            <Path d={paths.modules} fill={appearance.foreground} />
            <Path d={paths.eyes} fill={appearance.foreground} fillRule="evenodd" />
          </Svg>
          {appearance.logoUri ? (
            <View
              style={[
                styles.logoPlate,
                {
                  width: qrSize * 0.24,
                  height: qrSize * 0.24,
                  backgroundColor: appearance.background,
                },
              ]}
            >
              <Image
                source={{ uri: appearance.logoUri }}
                style={{ width: qrSize * 0.18, height: qrSize * 0.18 }}
                resizeMode="contain"
                accessibilityLabel="Logo image"
              />
            </View>
          ) : null}
        </View>
      ) : preview.kind === 'error' ? (
        <View style={styles.stateBox}>
          <Ionicons name="alert-circle-outline" size={56} color={colors.danger} />
          <Text style={[styles.stateText, { color: colors.danger }]} accessibilityRole="alert">
            {preview.message}
          </Text>
        </View>
      ) : (
        <View style={styles.stateBox}>
          <Ionicons name="qr-code-outline" size={56} color={colors.textMuted} />
          <Text style={[styles.stateText, { color: colors.textMuted }]}>
            Fill in the fields to build your QR code.
          </Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderWidth: 1,
    borderRadius: RADIUS.lg,
    padding: SPACING.lg,
    alignItems: 'center',
    alignSelf: 'stretch',
  },
  qrBox: {
    overflow: 'hidden',
    borderRadius: RADIUS.sm,
  },
  logoPlate: {
    position: 'absolute',
    alignSelf: 'center',
    top: 0,
    bottom: 0,
    margin: 'auto',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: RADIUS.sm,
  },
  stateBox: {
    minHeight: 200,
    alignItems: 'center',
    justifyContent: 'center',
    gap: SPACING.md,
    paddingHorizontal: SPACING.lg,
  },
  stateText: {
    fontSize: TYPE.label,
    textAlign: 'center',
  },
});
