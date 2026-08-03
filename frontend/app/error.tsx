"use client";

type ErrorPageProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

const pageStyle = {
  minHeight: "100vh",
  display: "grid",
  placeItems: "center",
  padding: "24px",
  background: "#f5f7fb",
  color: "#1e2735",
  fontFamily: '"Manrope", "Segoe UI", sans-serif',
} as const;

const cardStyle = {
  width: "min(100%, 560px)",
  padding: "36px",
  border: "1px solid #d9e0ea",
  borderRadius: "18px",
  background: "#ffffff",
  boxShadow: "0 24px 60px rgba(18, 32, 52, 0.12)",
  textAlign: "center",
} as const;

export default function ErrorPage({ reset }: ErrorPageProps) {
  return (
    <main style={pageStyle}>
      <section aria-labelledby="runtime-error-title" role="alert" style={cardStyle}>
        <div
          aria-hidden="true"
          style={{
            width: 52,
            height: 52,
            display: "grid",
            placeItems: "center",
            margin: "0 auto 20px",
            borderRadius: 16,
            background: "#fff2dc",
            color: "#a8610d",
            fontSize: 26,
            fontWeight: 800,
          }}
        >
          !
        </div>
        <h1
          id="runtime-error-title"
          style={{ margin: "0 0 10px", fontSize: 28, lineHeight: 1.2, letterSpacing: "-0.02em" }}
        >
          Не удалось открыть этот экран
        </h1>
        <p style={{ margin: "0 auto 24px", maxWidth: 440, color: "#66758a", lineHeight: 1.6 }}>
          Данные не потеряны. Обновите экран — чаще всего этого достаточно, чтобы продолжить работу.
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: 10 }}>
          <button
            type="button"
            onClick={reset}
            style={{
              minWidth: 150,
              border: 0,
              borderRadius: 10,
              padding: "11px 18px",
              background: "#2f4666",
              color: "#ffffff",
              font: "inherit",
              fontWeight: 750,
              cursor: "pointer",
            }}
          >
            Попробовать снова
          </button>
          <button
            type="button"
            onClick={() => window.location.assign("/")}
            style={{
              minWidth: 150,
              border: "1px solid #d9e0ea",
              borderRadius: 10,
              padding: "10px 18px",
              background: "#f1f4f8",
              color: "#2f4666",
              font: "inherit",
              fontWeight: 750,
              cursor: "pointer",
            }}
          >
            На главную
          </button>
        </div>
      </section>
    </main>
  );
}
