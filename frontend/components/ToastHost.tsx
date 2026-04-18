"use client";

import { ToastItem } from "../hooks/useToast";

export function ToastHost({ toasts }: { toasts: ToastItem[] }) {
  return (
    <div className="toast-host">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.type}${t.hide ? " hide" : ""}`}>
          {t.message}
        </div>
      ))}
    </div>
  );
}
