import type { RefCallback, RefObject } from 'react';

export default function useComposedRef<T>(
  internalRef: RefObject<T | null> | RefCallback<T>,
  forwardedRef?: RefObject<T | null> | RefCallback<T> | null,
): RefCallback<T>;
