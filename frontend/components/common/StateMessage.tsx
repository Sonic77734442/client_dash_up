"use client";

export function StateMessage({ title, message }: { title: string; message: string }) {
  return (
    <div className="insight-card" style={{ borderLeftColor: "#8296b1" }}>
      <div className="insight-title">{title}</div>
      <div className="insight-text">{message}</div>
    </div>
  );
}
