"use client";

import { StateMessage } from "../common/StateMessage";
import { TimelineChart } from "../TimelineChart";
import { AccountBreakdown, OperationalAction, OperationalInsight, Overview, PlatformBreakdown, TimelineAction, TimelinePoint } from "../../lib/types";

type DashboardViewProps = {
  overview: Overview | null;
  platform: "all" | "meta" | "google" | "tiktok";
  platformRows: PlatformBreakdown[];
  riskRows: AccountBreakdown[];
  periodDays: number;
  groupedTimeline: TimelinePoint[];
  timelineActions: TimelineAction[];
  operationalInsights: OperationalInsight[];
  recentActions: OperationalAction[];
  fmtMoney: (v: number | null | undefined) => string;
  fmtNum: (v: number | null | undefined) => string;
  paceClass: (status: string) => string;
  onInsightAction: (row: OperationalInsight) => Promise<void>;
  onRiskActionDraft: (accountId: string, label: string) => void;
};

export function DashboardView({
  overview,
  platform,
  platformRows,
  riskRows,
  periodDays,
  groupedTimeline,
  timelineActions,
  operationalInsights,
  recentActions,
  fmtMoney,
  fmtNum,
  paceClass,
  onInsightAction,
  onRiskActionDraft,
}: DashboardViewProps) {
  return (
    <>
      <section className="kpi-grid">
        {(() => {
          const b = overview?.budget_summary;
          const spend = overview?.spend_summary;
          if (!overview || !b || !spend) return null;

          const singleMode = platform !== "all";
          if (singleMode) {
            const p = platformRows[0] || {
              platform,
              spend: 0,
              impressions: 0,
              clicks: 0,
              conversions: 0,
              ctr: 0,
              cpc: 0,
              cpm: 0,
            };
            const cards = [
              { title: `${String(p.platform).toUpperCase()} Spend`, value: fmtMoney(p.spend), badge: b.pace_status },
              { title: "Impressions", value: fmtNum(p.impressions), badge: "on_track" },
              { title: "Clicks", value: fmtNum(p.clicks), badge: "on_track" },
              { title: "Conversions", value: fmtNum(p.conversions), badge: Number(p.conversions || 0) > 0 ? "on_track" : "underspending" },
            ];
            return cards.map((c) => (
              <article key={c.title} className={`kpi-card ${paceClass(c.badge)}`}>
                <div className="kpi-head">
                  <div className="kpi-title">{c.title}</div>
                  <span className={`badge ${paceClass(c.badge)}`}>{String(c.badge).replace("_", " ")}</span>
                </div>
                <div className="kpi-value">{c.value}</div>
                <div className="kpi-meta">
                  <div>
                    <div>Remaining</div>
                    <strong>{b.remaining == null ? "--" : fmtMoney(b.remaining)}</strong>
                  </div>
                  <div>
                    <div>Usage</div>
                    <strong>{b.usage_percent == null ? "--" : `${b.usage_percent.toFixed(1)}%`}</strong>
                  </div>
                </div>
                <div className="kpi-meta">
                  <div>{`Budget ${fmtMoney(b.budget || 0)}`}</div>
                  <div>{`Forecast ${fmtMoney(b.forecast_spend || 0)}`}</div>
                </div>
              </article>
            ));
          }

          const topCards = [
            {
              title: "Total Spend",
              value: fmtMoney(spend.spend),
              status: b.pace_status,
              leftLabel: "Remaining",
              leftValue: b.remaining == null ? "--" : fmtMoney(b.remaining),
              rightLabel: "Usage",
              rightValue: b.usage_percent == null ? "--" : `${b.usage_percent.toFixed(1)}%`,
              footL: `Budget ${fmtMoney(b.budget || 0)}`,
              footR: `Forecast ${fmtMoney(b.forecast_spend || 0)}`,
            },
            ...platformRows.slice(0, 3).map((p) => ({
              title: p.platform.toUpperCase(),
              value: fmtMoney(p.spend),
              status: p.cpc > 3 ? "overspending" : "on_track",
              leftLabel: "Clicks",
              leftValue: fmtNum(p.clicks),
              rightLabel: "CTR",
              rightValue: `${(p.ctr * 100).toFixed(1)}%`,
              footL: `CPC ${fmtMoney(p.cpc)}`,
              footR: `CPM ${fmtMoney(p.cpm)}`,
            })),
          ];
          while (topCards.length < 4) {
            topCards.push({
              title: "No Data",
              value: fmtMoney(0),
              status: "on_track",
              leftLabel: "--",
              leftValue: "--",
              rightLabel: "--",
              rightValue: "--",
              footL: "Select another platform",
              footR: "",
            });
          }
          return topCards.slice(0, 4).map((c) => (
            <article key={c.title + c.value} className={`kpi-card ${paceClass(c.status)}`}>
              <div className="kpi-head">
                <div className="kpi-title">{c.title}</div>
                <span className={`badge ${paceClass(c.status)}`}>{String(c.status).replace("_", " ")}</span>
              </div>
              <div className="kpi-value">{c.value}</div>
              <div className="kpi-meta">
                <div>
                  <div>{c.leftLabel}</div>
                  <strong>{c.leftValue}</strong>
                </div>
                <div>
                  <div>{c.rightLabel}</div>
                  <strong>{c.rightValue}</strong>
                </div>
              </div>
              <div className="kpi-meta">
                <div>{c.footL}</div>
                <div>{c.footR}</div>
              </div>
            </article>
          ));
        })()}
      </section>

      <section className="mid-grid">
        <article className="panel">
          <h3>Daily Spend Timeline</h3>
          <div className="panel-subtitle">Expected vs actual performance trajectory</div>
          <div className="chart">
            <TimelineChart
              points={groupedTimeline}
              budgetCap={overview?.budget_summary?.budget}
              asOfDate={overview?.range?.as_of_date}
              actions={timelineActions}
            />
          </div>
        </article>

        <article className="panel contribution">
          <h3>Contribution</h3>
          <div>
            {(() => {
              const total = platformRows.reduce((sum, x) => sum + Number(x.spend || 0), 0) || 1;
              return platformRows.map((x) => {
                const share = (Number(x.spend || 0) / total) * 100;
                return (
                  <div key={x.platform} className="contribution-item">
                    <div className="row">
                      <span>{x.platform.toUpperCase()}</span>
                      <span>{share.toFixed(1)}%</span>
                    </div>
                    <div className="bar">
                      <div style={{ width: `${share.toFixed(1)}%` }}></div>
                    </div>
                    <div className="row" style={{ marginTop: 4, color: "#738093", fontSize: 12 }}>
                      Efficiency {(x.ctr * 100).toFixed(1)}%
                    </div>
                  </div>
                );
              });
            })()}
          </div>
        </article>
      </section>

      <section className="bottom-grid">
        <article className="panel risk-center">
          <div className="panel-head">
            <h3>Account Risk Center</h3>
          </div>
          <table>
            <thead>
              <tr>
                <th>Account</th>
                <th>Platform</th>
                <th>Daily Spend</th>
                <th>Pace Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {riskRows.map((r) => {
                const rec = r.cpc > 3 ? { label: "Cap -10%", cls: "cap" } : r.ctr < 0.03 ? { label: "Pause", cls: "pause" } : { label: "Scale +10%", cls: "scale" };
                const pace = r.cpc > 3 ? "critical_overspend" : r.ctr < 0.03 ? "warning_pace" : "efficient_scale";
                return (
                  <tr key={r.account_id}>
                    <td>
                      <strong>{r.name || r.account_id.slice(0, 8)}</strong>
                    </td>
                    <td>{r.platform.toUpperCase()}</td>
                    <td>{fmtMoney(Number(r.spend || 0) / Math.max(1, periodDays))}</td>
                    <td>
                      <span className={`badge ${paceClass(pace.includes("critical") ? "overspending" : pace.includes("warning") ? "underspending" : "on_track")}`}>
                        {pace.replaceAll("_", " ")}
                      </span>
                    </td>
                    <td>
                      <button className={`action-btn ${rec.cls}`} onClick={() => onRiskActionDraft(r.account_id, rec.label)}>
                        {rec.label}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </article>

        <div className="side-stack">
          <article className="panel insights">
            <h3>Operational Insights</h3>
            {!operationalInsights.length ? (
              <StateMessage title="No recommendations in current scope" message="Try another period/client/platform or wait for more data." />
            ) : (
              operationalInsights.slice(0, 3).map((row) => {
                const border = row.priority === "high" ? "#d14f4f" : row.priority === "medium" ? "#d18a3d" : "#22a35a";
                const cta = row.action === "scale" ? "Execute Scaling" : row.action === "cap" ? "Apply Spend Cap" : row.action === "pause" ? "Pause Assets" : "Review Strategy";
                return (
                  <div key={`${row.action}-${row.scope_id}`} className="insight-card" style={{ borderLeftColor: border }}>
                    <div className="insight-title">{row.title}</div>
                    <div className="insight-text">{row.reason}</div>
                    <div className="insight-text" style={{ marginTop: 4 }}>
                      Priority: {row.priority.toUpperCase()} • Score: {Number(row.score || 0).toFixed(2)}
                    </div>
                    <button className="ghost-btn" style={{ marginTop: 8 }} onClick={() => void onInsightAction(row)}>
                      {cta}
                    </button>
                  </div>
                );
              })
            )}
          </article>
          <article className="panel recent-actions">
            <h3>Recent Actions</h3>
            {!recentActions.length ? (
              <div className="action-meta">No actions yet for current scope.</div>
            ) : (
              recentActions.slice(0, 5).map((x) => {
                const status = String(x.status || "queued");
                const scope = String(x.scope || "account").toUpperCase();
                const action = String(x.action || "").toUpperCase();
                const dt = new Date(x.created_at);
                const ts = Number.isNaN(dt.getTime()) ? x.created_at : dt.toLocaleString();
                return (
                  <div key={x.id} className="action-row timeline-item">
                    <div className="action-row-head">
                      <div className="action-title">{`${action} • ${scope}`}</div>
                      <span className={`status-pill ${status}`}>{status.toUpperCase()}</span>
                    </div>
                    <div className="action-meta">{x.title || "--"}</div>
                    <div className="action-meta">{x.scope_id || "--"}</div>
                    <div className="action-meta">{ts}</div>
                  </div>
                );
              })
            )}
          </article>
        </div>
      </section>
    </>
  );
}
