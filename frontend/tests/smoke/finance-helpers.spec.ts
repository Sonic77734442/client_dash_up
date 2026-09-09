import { expect, test } from "@playwright/test";
import { budgetPace } from "../../lib/budgetPacing";
import { formatCurrency, scopedCurrencyComponents, sumByCurrency } from "../../lib/currency";
import { TimelineChart } from "../../components/TimelineChart";
import { DashboardView } from "../../components/views/DashboardView";
import type { OperationalInsight } from "../../lib/types";

test("pacing uses elapsed UTC days and the selected share of a full budget", () => {
  const period = {
    amount: 3000, start: "2026-09-01", end: "2026-09-30",
    rangeFrom: "2026-09-01", rangeTo: "2026-09-30", asOf: "2026-09-02",
  };
  expect(budgetPace({ ...period, spend: 1200 })).toBe("overspending");
  expect(budgetPace({ ...period, spend: 2850 })).toBe("overspending");
  expect(budgetPace({ ...period, spend: 200 })).toBe("on_track");
  expect(budgetPace({ ...period, spend: 179 })).toBe("underspending");
  expect(budgetPace({ ...period, spend: 220 })).toBe("on_track");
  expect(budgetPace({ ...period, spend: 200, rangeFrom: "2026-09-10", asOf: "2026-09-11" })).toBe("on_track");
  expect(budgetPace({ ...period, spend: 3000, asOf: "2026-10-05" })).toBe("on_track");
  expect(budgetPace({ ...period, spend: 0, asOf: "2026-08-31" })).toBe("unknown");
  expect(budgetPace({ ...period, spend: NaN })).toBe("unknown");
});

test("money stays in its native currency and mixed totals are never added", () => {
  const totals = sumByCurrency([
    { currency: "USD", spend: 100 }, { currency: "KZT", spend: 50000 },
    { currency: "USD", spend: 200 }, { currency: null, spend: null },
  ]);
  expect([...totals.entries()]).toEqual([["USD", 300], ["KZT", 50000]]);
  expect(formatCurrency(100, "USD")).toContain("$");
  expect(formatCurrency(100, "USD")).not.toContain("₸");
  expect(formatCurrency(100, "KZT")).toMatch(/KZT|₸/);
  expect(formatCurrency(null, "USD")).toBe("—");
  expect(formatCurrency(50100, null)).toBe("Разные валюты");
  const chart = TimelineChart({
    currency: "USD", points: [{ date: "2026-09-01", label: "09-01", actual: 100, expected: 100 }],
  });
  expect(chart.props.option.yAxis.axisLabel.formatter(100)).toContain("$");
  expect(chart.props.option.tooltip.valueFormatter(100)).not.toContain("₸");
  const selectedPortfolio = scopedCurrencyComponents(
    [{ client_id: "mixed", spend: null, currency: null }],
    [
      { client_id: "mixed", spend: 20, currency: "USD" },
      { client_id: "mixed", spend: 10000, currency: "KZT" },
      { client_id: "other-agency", spend: 999999, currency: "USD" },
    ],
  );
  expect([...sumByCurrency(selectedPortfolio)]).toEqual([["USD", 20], ["KZT", 10000]]);
});

test("dashboard never invents a restriction from nominal CPC in a different currency", () => {
  const render = (operationalInsights: OperationalInsight[]) => JSON.stringify(DashboardView({
    overview: null, currency: "KZT", dataState: "current", dataNotice: "", platform: "all",
    platformRows: [], periodDays: 30, groupedTimeline: [], timelineActions: [], recentActions: [],
    operationalInsights,
    riskRows: [{
      account_id: "account", client_id: "client", name: "KZT account", platform: "meta", currency: "KZT",
      spend: 100000, clicks: 100, impressions: 10000, conversions: 10, ctr: 0.01, cpc: 1000, cpm: 10000,
    }],
    fmtMoney: (value) => formatCurrency(value, "KZT"),
    fmtScopedMoney: (value) => formatCurrency(value, "KZT"), fmtNum: (value) => String(value ?? "—"),
    paceClass: (status) => status,
    onInsightAction: async () => {}, onRiskActionDraft: async () => {},
  }));
  const neutral = render([]);
  expect(neutral).toContain("Нет подтверждённого отклонения");
  expect(neutral).not.toContain("Создать задачу: ограничить");
  const recommended = render([{
    scope: "account", scope_id: "account", action: "scale", priority: "medium", score: 75,
    title: "Подтверждён потенциал роста", reason: "Сравнение с аккаунтами в той же валюте", metrics: {},
  }]);
  expect(recommended).toContain("Создать задачу: масштабировать");
  expect(recommended).not.toContain("Создать задачу: ограничить");
});
