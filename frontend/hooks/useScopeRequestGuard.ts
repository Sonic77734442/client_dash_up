"use client";

import { useCallback, useLayoutEffect, useRef } from "react";

/** Keeps a late response from an old tenant scope from replacing the current screen. */
export function useScopeRequestGuard(scopeKey: string) {
  const scopeRef = useRef(scopeKey);
  const requestEpochRef = useRef(0);
  useLayoutEffect(() => {
    // Only committed navigation changes the active scope. An abandoned
    // concurrent render must not invalidate the screen still being shown.
    scopeRef.current = scopeKey;
    requestEpochRef.current += 1;
    return () => { requestEpochRef.current += 1; };
  }, [scopeKey]);

  return useCallback(() => {
    const requestedScope = scopeKey;
    const requestEpoch = ++requestEpochRef.current;
    return () => (
      scopeRef.current === requestedScope && requestEpochRef.current === requestEpoch
    );
  }, [scopeKey]);
}
