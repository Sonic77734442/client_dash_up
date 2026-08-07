"use client";

import { useCallback, useRef } from "react";

/** Keeps a late response from an old tenant scope from replacing the current screen. */
export function useScopeRequestGuard(scopeKey: string) {
  const scopeRef = useRef(scopeKey);
  const requestEpochRef = useRef(0);
  scopeRef.current = scopeKey;

  return useCallback(() => {
    const requestedScope = scopeKey;
    const requestEpoch = ++requestEpochRef.current;
    return () => (
      scopeRef.current === requestedScope && requestEpochRef.current === requestEpoch
    );
  }, [scopeKey]);
}
