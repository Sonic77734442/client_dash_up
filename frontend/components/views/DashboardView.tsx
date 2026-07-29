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
  onRiskActionDraft: (accountId: string, label: string) => Promise<void>;
};

function insightCopy(row: OperationalInsight | null) {
  if (!row) {
    return {
      title: "Показатели стабильны",
      reason: "Критических отклонений в выбранном контуре не найдено. Продолжайте наблюдение.",
    };
  }

  const metric = (key: string) => Number(row.metrics?.[key] || 0);
  const platform = String(row.metrics?.platform || "").toUpperCase();

  if (row.metrics?.fallback) {
    return {
      title: "Срочных действий не требуется",
      reason: "Показатели находятся внутри заданных порогов. Система продолжит отслеживать изменения.",
    };
  }
  if (row.action === "cap") {
    return {
      title: `Ограничить расход${platform ? ` в ${platform}` : ""}`,
      reason: `Стоимость клика ${metric("cpc").toFixed(2)} KZT выше обычного уровня, при этом аккаунт формирует ${(metric("spend_share") * 100).toFixed(1)}% расхода.`,
    };
  }
  if (row.action === "scale") {
    return {
      title: `Рассмотреть масштабирование${platform ? ` в ${platform}` : ""}`,
      reason: `CTR ${(metric("ctr") * 100).toFixed(2)}% выше среднего, а стоимость клика ${metric("cpc").toFixed(2)} KZT остаётся эффективной.`,
    };
  }
  if (row.metrics?.pace_delta_percent != null) {
    return {
      title: "Проверить темп расходования бюджета",
      reason: `Фактический темп отличается от ожидаемой траектории на ${metric("pace_delta_percent").toFixed(1)}%.`,
    };
  }
  if (row.action === "review" && row.metrics?.ctr != null) {
    return {
      title: "Проверить объявления и креативы",
      reason: `CTR ${(metric("ctr") * 100).toFixed(2)}% ниже среднего уровня по сопоставимым аккаунтам.`,
    };
  }
  return { title: row.title, reason: row.reason };
}

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
  const spend = Number(overview?.spend_summary?.spend || 0);
  const conversions = Number(overview?.spend_summary?.conversions || 0);
  const cpl = conversions > 0 ? spend / conversions : null;
  const attentionCount = operationalInsights.filter((row) => row.priority === "high" || row.priority === "medium").length;
  const leadInsight = operationalInsights[0] || null;
  const leadCopy = insightCopy(leadInsight);
  const contributionTotal = platformRows.reduce((sum, row) => sum + Number(row.spend || 0), 0) || 1;

  return (
    <>
      <section className="kpi-grid">
        <article className="kpi-card">
          <div className="kpi-title">Расход за период</div>
          <div className="kpi-value">{fmtMoney(spend)}</div>
          <div className="kpi-meta">Бюджет: {overview?.budget_summary?.budget == null ? "не задан" : fmtMoney(overview.budget_summary.budget)}</div>
        </article>
        <article className="kpi-card">
          <div className="kpi-title">Конверсии</div>
          <div className="kpi-value">{fmtNum(conversions)}</div>
          <div className="kpi-meta">{platform === "all" ? "Все рекламные платформы" : platform.toUpperCase()}</div>
        </article>
        <article className="kpi-card">
          <div className="kpi-title">Стоимость конверсии</div>
          <div className="kpi-value">{cpl == null ? "—" : fmtMoney(cpl)}</div>
          <div className="kpi-meta">Фактическая стоимость за выбранный период</div>
        </article>
        <article className="kpi-card">
          <div className="kpi-title">Требуют внимания</div>
          <div className="kpi-value">{attentionCount}</div>
          <div className="kpi-meta">Отклонения, для которых есть рекомендация</div>
        </article>
      </section>

      <section className="mid-grid blueprint-main-grid">
        <article className="panel">
          <h3>Динамика расходов</h3>
          <div className="panel-subtitle">Фактический расход относительно ожидаемой траектории</div>
          <div className="chart">
            <TimelineChart
              points={groupedTimeline}
              budgetCap={overview?.budget_summary?.budget}
              asOfDate={overview?.range?.as_of_date}
              actions={timelineActions}
            />
          </div>
        </article>

        <article className="panel performance-summary">
          <h3>Главное за период</h3>
          <div className={`blueprint-note ${leadInsight?.priority === "high" ? "bad" : ""}`}>
            <strong>{leadCopy.title}</strong>
            <p className="panel-subtitle">
              {leadCopy.reason}
            </p>
            {leadInsight && !leadInsight.metrics?.fallback ? (
              <button className="primary-btn" onClick={() => void onInsightAction(leadInsight)}>
                Создать действие
              </button>
            ) : null}
          </div>
          <div className="contribution">
            <div className="panel-subtitle">Вклад платформ в расход</div>
            {platformRows.map((row) => {
              const share = (Number(row.spend || 0) / contributionTotal) * 100;
              return (
                <div key={row.platform} className="contribution-item">
                  <div className="row">
                    <span>{row.platform.toUpperCase()}</span>
                    <span>{share.toFixed(1)}%</span>
                  </div>
                  <div className="bar"><div style={{ width: `${share.toFixed(1)}%` }} /></div>
                </div>
              );
            })}
            {!platformRows.length ? (
              <div className="action-meta">Появится после первой синхронизации рекламных данных.</div>
            ) : null}
          </div>
        </article>
      </section>

      <section className="bottom-grid">
        <article className="panel risk-center">
          <div className="panel-head">
            <div>
              <h3>Аккаунты, требующие решения</h3>
              <div className="panel-subtitle">Причина, влияние и быстрое действие по каждому аккаунту</div>
            </div>
          </div>
          <table>
            <thead>
              <tr>
                <th>Аккаунт</th>
                <th>Платформа</th>
                <th>Расход в день</th>
                <th>Состояние</th>
                <th>Действие</th>
              </tr>
            </thead>
            <tbody>
              {riskRows.map((r) => {
                const rec = r.cpc > 3 ? { label: "Ограничить −10%", cls: "cap" } : r.ctr < 0.03 ? { label: "Проверить", cls: "pause" } : { label: "Масштабировать +10%", cls: "scale" };
                const paceLabel = r.cpc > 3 ? "Высокая стоимость" : r.ctr < 0.03 ? "Низкий CTR" : "Можно масштабировать";
                const status = r.cpc > 3 ? "overspending" : r.ctr < 0.03 ? "underspending" : "on_track";
                return (
                  <tr key={r.account_id}>
                    <td>
                      <strong>{r.name || r.account_id.slice(0, 8)}</strong>
                    </td>
                    <td>{r.platform.toUpperCase()}</td>
                    <td>{fmtMoney(Number(r.spend || 0) / Math.max(1, periodDays))}</td>
                    <td>
                      <span className={`badge ${paceClass(status)}`}>
                        {paceLabel}
                      </span>
                    </td>
                    <td>
                      <button className={`action-btn ${rec.cls}`} onClick={() => void onRiskActionDraft(r.account_id, rec.label)}>
                        {rec.label}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!riskRows.length ? <tr><td colSpan={5}>В выбранном контуре нет аккаунтов с данными.</td></tr> : null}
            </tbody>
          </table>
        </article>

        <div className="side-stack">
          <article className="panel recent-actions">
            <h3>Сейчас в работе</h3>
            {!recentActions.length ? (
              <div className="action-meta">Активных действий в выбранном контуре нет.</div>
            ) : (
              recentActions.slice(0, 5).map((x) => {
                const status = String(x.status || "queued");
                const scope = String(x.scope || "account") === "client" ? "КЛИЕНТ" : "АККАУНТ";
                const actionNames: Record<string, string> = { cap: "ОГРАНИЧЕНИЕ", pause: "ПАУЗА", scale: "МАСШТАБИРОВАНИЕ", review: "ПРОВЕРКА" };
                const action = actionNames[String(x.action || "")] || String(x.action || "").toUpperCase();
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

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head">
          <div>
            <h3>Отклонения и рекомендации</h3>
            <div className="panel-subtitle">Система объясняет причину и предлагает следующий шаг</div>
          </div>
        </div>
        {!operationalInsights.length ? (
          <StateMessage title="Рекомендаций пока нет" message="Выберите другой период или дождитесь новых данных." />
        ) : (
          operationalInsights.slice(0, 5).map((row) => {
            const copy = insightCopy(row);
            const priorityLabel = row.priority === "high" ? "Высокий" : row.priority === "medium" ? "Средний" : "Наблюдение";
            return (
              <div key={`${row.action}-${row.scope_id}`} className={`insight-card ${row.priority === "high" ? "bad" : ""}`}>
                <div className="insight-head">
                  <div className="insight-title">{copy.title}</div>
                  <span className={`badge ${row.priority === "high" ? "bad" : row.priority === "medium" ? "warn" : "good"}`}>{priorityLabel}</span>
                </div>
                <div className="insight-text">{copy.reason}</div>
                {!row.metrics?.fallback ? (
                  <button className="ghost-btn" style={{ marginTop: 8 }} onClick={() => void onInsightAction(row)}>Взять в работу</button>
                ) : null}
              </div>
            );
          })
        )}
      </section>
    </>
  );
}
