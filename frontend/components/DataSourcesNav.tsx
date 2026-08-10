"use client";

import Link from "next/link";
import { useSessionContext } from "../hooks/useSessionContext";

type DataSourcesSection = "overview" | "accounts" | "sync";

const sections: Array<{
  key: DataSourcesSection;
  href: string;
  step: string;
  title: string;
  description: string;
}> = [
  {
    key: "overview",
    href: "/integrations",
    step: "1",
    title: "Подключения",
    description: "Meta, Google Ads и TikTok",
  },
  {
    key: "accounts",
    href: "/accounts",
    step: "2",
    title: "Рекламные аккаунты",
    description: "Импорт и привязка к клиентам",
  },
  {
    key: "sync",
    href: "/sync-monitor",
    step: "3",
    title: "Синхронизация",
    description: "Обновление данных и ошибки",
  },
];

export function DataSourcesNav({ active }: { active: DataSourcesSection }) {
  const { context } = useSessionContext();
  const soloClient = context?.role === "solo_client";
  return (
    <nav className="data-sources-nav" aria-label="Разделы источников рекламы">
      {sections.map((section) => {
        const selected = section.key === active;
        return (
          <Link
            key={section.key}
            href={section.href}
            className={`data-sources-nav-item ${selected ? "active" : ""}`.trim()}
            aria-current={selected ? "page" : undefined}
          >
            <span className="data-sources-nav-step">{section.step}</span>
            <span className="data-sources-nav-copy">
              <strong>{section.title}</strong>
              <small>
                {soloClient && section.key === "accounts"
                  ? "Ваши подключённые кабинеты"
                  : soloClient && section.key === "sync"
                    ? "Обновление ваших данных и ошибки"
                    : section.description}
              </small>
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
