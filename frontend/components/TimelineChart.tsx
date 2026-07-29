"use client";

import ReactECharts from "echarts-for-react";

type Point = { date: string; label: string; expected: number; actual: number | null };
type ActionMarker = { date: string; action: string; title?: string };

const formatKzt = (value: unknown) =>
  new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "KZT",
    maximumFractionDigits: 0,
  }).format(Number(value || 0));

export function TimelineChart({
  points,
  budgetCap,
  asOfDate,
  actions = [],
}: {
  points: Point[];
  budgetCap?: number | null;
  asOfDate?: string | null;
  actions?: ActionMarker[];
}) {
  if (!points.length) {
    return (
      <div className="chart-empty" style={{ color: "#77797d", fontWeight: 500 }}>
        Нет данных за выбранный период
      </div>
    );
  }

  const hasValues = points.some((point) => Number(point.actual || 0) > 0 || Number(point.expected || 0) > 0);
  if (!hasValues) {
    return (
      <div className="chart-empty" style={{ color: "#77797d", fontWeight: 500 }}>
        За выбранный период расходов пока нет
      </div>
    );
  }

  const labelsByDate = new Map(points.map((x) => [x.date, x.label]));
  const labels = points.map((x) => x.label);
  const expected = points.map((x) => x.expected);
  const actual = points.map((x) => x.actual);
  const lastActualIndex = (() => {
    for (let i = actual.length - 1; i >= 0; i -= 1) {
      if (actual[i] != null) return i;
    }
    return actual.length - 1;
  })();
  const lastValue = Number(actual[lastActualIndex] || 0);
  const expectedLast = expected[expected.length - 1] || 0;
  const cap = Number(budgetCap || 0) > 0 ? Number(budgetCap || 0) : expectedLast;
  const actualNums = actual.map((x) => Number(x || 0));
  const maxBase = Math.max(1, cap, lastValue, ...actualNums, ...expected);
  const cap90 = cap * 0.9;
  const cap120 = cap * 1.2;
  const yMax = Math.max(maxBase * 1.1, cap120 * 1.05);

  const asOfLabel = asOfDate ? labelsByDate.get(asOfDate) || null : null;
  const asOfIndex = asOfLabel ? labels.indexOf(asOfLabel) : labels.length - 1;

  const coneMin = expected.map((v, i) => (i >= asOfIndex ? v * 0.9 : null));
  const coneBand = expected.map((v, i) => (i >= asOfIndex ? v * 0.2 : null));

  const actionEvents = (actions || [])
    .map((a) => {
      const label = labelsByDate.get(a.date);
      if (!label) return null;
      const idx = labels.indexOf(label);
      if (idx < 0) return null;
      // Keep right-edge area clear for the last-value label.
      if (idx >= lastActualIndex - 1) return null;
      return {
        value: [label, actual[idx] ?? 0],
        action: String(a.action || "").toUpperCase(),
        title: a.title || "Операционное действие",
      };
    })
    .filter(Boolean);

  return (
    <ReactECharts
      style={{ height: "100%", width: "100%" }}
      option={{
        animationDuration: 450,
        grid: { left: 20, right: 18, top: 28, bottom: 20, containLabel: true },
        tooltip: {
          trigger: "axis",
          backgroundColor: "#ffffff",
          borderColor: "rgba(26,28,31,0.08)",
          borderWidth: 1,
          textStyle: { color: "#1a1c1f", fontSize: 12, fontWeight: 500 },
          extraCssText: "border-radius:12px;box-shadow:none;",
          valueFormatter: formatKzt,
        },
        legend: {
          data: ["Плановый расход", "Фактический расход"],
          right: 12,
          top: 0,
          textStyle: { color: "#77797d", fontWeight: 500, fontSize: 11 },
        },
        xAxis: {
          type: "category",
          data: labels,
          boundaryGap: false,
          axisLine: { lineStyle: { color: "#e5e6e7" } },
          axisTick: { show: false },
          axisLabel: { color: "#85878b", fontSize: 10, fontWeight: 500 },
        },
        yAxis: {
          type: "value",
          min: 0,
          max: yMax,
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: "#f0f1f2" } },
          axisLabel: {
            color: "#85878b",
            fontSize: 10,
            fontWeight: 500,
            formatter: (v: number) => formatKzt(v),
          },
        },
        series: [
          {
            name: "Нижняя граница прогноза",
            type: "line",
            stack: "cone",
            data: coneMin,
            showSymbol: false,
            lineStyle: { opacity: 0 },
            itemStyle: { opacity: 0 },
            areaStyle: { opacity: 0 },
            markArea: {
              silent: true,
              data: [
                [{ yAxis: 0 }, { yAxis: cap90, itemStyle: { color: "rgba(51,156,255,0.035)" } }],
                [{ yAxis: cap90 }, { yAxis: cap, itemStyle: { color: "rgba(26,28,31,0.035)" } }],
                [{ yAxis: cap }, { yAxis: cap120, itemStyle: { color: "rgba(26,28,31,0.06)" } }],
              ],
            },
            z: 1,
          },
          {
            name: "Диапазон прогноза",
            type: "line",
            stack: "cone",
            data: coneBand,
            showSymbol: false,
            lineStyle: { opacity: 0 },
            itemStyle: { opacity: 0 },
            areaStyle: { color: "rgba(26,28,31,0.08)" },
            z: 1,
          },
          {
            name: "Плановый расход",
            type: "line",
            smooth: 0.32,
            data: expected,
            lineStyle: { color: "#a9abaf", width: 2, type: "dashed" },
            showSymbol: false,
            markLine: {
              silent: true,
              symbol: ["none", "none"],
              lineStyle: { color: "#c5c7ca", width: 1.5 },
              label: {
                show: true,
                color: "#77797d",
                fontSize: 10,
                fontWeight: 500,
                formatter: "Лимит бюджета",
              },
              data: [{ yAxis: cap }],
            },
          },
          {
            name: "Фактический расход",
            type: "line",
            smooth: 0.32,
            data: actual,
            lineStyle: { color: "#339cff", width: 3 },
            areaStyle: {
              color: {
                type: "linear",
                x: 0,
                y: 0,
                x2: 0,
                y2: 1,
                colorStops: [
                  { offset: 0, color: "rgba(51,156,255,0.20)" },
                  { offset: 1, color: "rgba(51,156,255,0.025)" },
                ],
              },
            },
            symbol: "circle",
            symbolSize: 6,
            itemStyle: { color: "#339cff" },
            markPoint: {
              symbol: "roundRect",
              symbolSize: [96, 22],
              label: { color: "#ffffff", fontSize: 10, fontWeight: 500, formatter: formatKzt(lastValue) },
              itemStyle: { color: "#1a1c1f" },
              data: [{ coord: [labels[lastActualIndex], lastValue], value: lastValue }],
            },
            markLine: {
              silent: true,
              symbol: ["none", "none"],
              lineStyle: { color: "#b8babd", width: 1, type: "dotted" },
              label: { show: false },
              data: asOfLabel ? [{ xAxis: asOfLabel }] : [],
            },
          },
          {
            name: "Действия",
            type: "scatter",
            data: actionEvents,
            symbol: "diamond",
            symbolSize: 10,
            itemStyle: { color: "#1a1c1f", opacity: 0.88 },
            label: { show: false },
            tooltip: {
              formatter: (p: { data?: { action?: string; title?: string } }) =>
                `${p?.data?.action || "ДЕЙСТВИЕ"} · ${p?.data?.title || "Операционное событие"}`,
            },
            emphasis: { scale: 1.15 },
            z: 6,
          },
        ],
      }}
      opts={{ renderer: "canvas" }}
    />
  );
}
