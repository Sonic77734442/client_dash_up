"use client";

import { useCallback, useMemo, useState } from "react";

export type ToastType = "info" | "success" | "error";
export type ToastItem = { id: number; message: string; type: ToastType; hide?: boolean };

export function useToast() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const push = useCallback((message: string, type: ToastType = "info") => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, hide: true } : t)));
    }, 2400);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 2700);
  }, []);

  return useMemo(() => ({ toasts, push }), [toasts, push]);
}
