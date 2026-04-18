"use client";

import ReactECharts from "echarts-for-react";

type Point = { date: string; label: string; expected: number; actual: number | null };
type ActionMarker = { date: string; action: string; title?: string };

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
    return <div className="chart-empty">No daily stats for selected scope</div>;
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
        title: a.title || "Operational action",
      };
    })
    .filter(Boolean);

  return (
    <ReactECharts
      style={{ height: "100%", width: "100%" }}
      option={{
        animationDuration: 450,
        grid: { left: 42, right: 18, top: 18, bottom: 30 },
        tooltip: {
          trigger: "axis",
          backgroundColor: "#1f2f47",
          borderWidth: 0,
          textStyle: { color: "#f5f7fb", fontSize: 12 },
          valueFormatter: (v: unknown) => `$${Math.round(Number(v || 0))}`,
        },
        legend: {
          data: ["Expected Projection", "Actual Daily Spend"],
          right: 12,
          top: 0,
          textStyle: { color: "#738093", fontWeight: 700, fontSize: 11 },
        },
        xAxis: {
          type: "category",
          data: labels,
          boundaryGap: false,
          axisLine: { lineStyle: { color: "#d7dee8" } },
          axisTick: { show: false },
          axisLabel: { color: "#7f8da2", fontSize: 10 },
        },
        yAxis: {
          type: "value",
          min: 0,
          max: yMax,
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: "#ebf0f6" } },
          axisLabel: { color: "#7f8da2", fontSize: 10, formatter: (v: number) => `$${Math.round(v)}` },
        },
        series: [
          {
            name: "Forecast Min",
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
                [{ yAxis: 0 }, { yAxis: cap90, itemStyle: { color: "rgba(34,163,90,0.06)" } }],
                [{ yAxis: cap90 }, { yAxis: cap, itemStyle: { color: "rgba(209,138,61,0.08)" } }],
                [{ yAxis: cap }, { yAxis: cap120, itemStyle: { color: "rgba(209,79,79,0.08)" } }],
              ],
            },
            z: 1,
          },
          {
            name: "Forecast Cone",
            type: "line",
            stack: "cone",
            data: coneBand,
            showSymbol: false,
            lineStyle: { opacity: 0 },
            itemStyle: { opacity: 0 },
            areaStyle: { color: "rgba(122,140,168,0.16)" },
            z: 1,
          },
          {
            name: "Expected Projection",
            type: "line",
            smooth: 0.32,
            data: expected,
            lineStyle: { color: "#a6b2c3", width: 2, type: "dashed" },
            showSymbol: false,
            markLine: {
              silent: true,
              symbol: ["none", "none"],
              lineStyle: { color: "#b7c4d6", width: 1.5 },
              label: { show: true, color: "#738093", fontSize: 10, formatter: "Budget Cap" },
              data: [{ yAxis: cap }],
            },
          },
          {
            name: "Actual Daily Spend",
            type: "line",
            smooth: 0.32,
            data: actual,
            lineStyle: { color: "#2f4666", width: 3 },
            areaStyle: {
              color: {
                type: "linear",
                x: 0,
                y: 0,
                x2: 0,
                y2: 1,
                colorStops: [
                  { offset: 0, color: "rgba(47,70,102,0.22)" },
                  { offset: 1, color: "rgba(47,70,102,0.03)" },
                ],
              },
            },
            symbol: "circle",
            symbolSize: 6,
            itemStyle: { color: "#2f4666" },
            markPoint: {
              symbol: "roundRect",
              symbolSize: [58, 20],
              label: { color: "#ffffff", fontSize: 10, formatter: `$${Math.round(lastValue)}` },
              itemStyle: { color: "#2f4666" },
              data: [{ coord: [labels[lastActualIndex], lastValue], value: lastValue }],
            },
            markLine: {
              silent: true,
              symbol: ["none", "none"],
              lineStyle: { color: "#8aa0bd", width: 1, type: "dotted" },
              label: { show: false },
              data: asOfLabel ? [{ xAxis: asOfLabel }] : [],
            },
          },
          {
            name: "Action Events",
            type: "scatter",
            data: actionEvents,
            symbol: "diamond",
            symbolSize: 10,
            itemStyle: { color: "#d14f4f", opacity: 0.9 },
            label: { show: false },
            tooltip: {
              formatter: (p: { data?: { action?: string; title?: string } }) =>
                `${p?.data?.action || "ACTION"} • ${p?.data?.title || "Operational event"}`,
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
