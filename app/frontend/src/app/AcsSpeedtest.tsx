import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

type Kind = "download" | "upload";

interface SpeedResult {
  state: string; bytes: number | null; seconds: number | null; mbps: number | null; short?: boolean; began_at: string | null;
  /** Time-based tests: Mbps of each interval, the best one and the speed held in the second half (after a burst). */
  intervals?: number[] | null; peak_mbps?: number | null; sustained_mbps?: number | null;
}

interface SpeedTest {
  state: "requested" | "complete" | "error" | "timeout";
  server: "nsm" | "custom";
  host: string | null;
  size_mb: number | null;
  seconds?: number | null;
  requested_at: string;
  completed_at?: string | null;
  by: string | null;
  started: boolean;
  error?: string | null;
  result: SpeedResult | null;
}

interface AcsSpeedtestData {
  linked: boolean;
  can_run: boolean;
  model: "tr181" | "tr098" | null;
  size_mb: number;
  custom: { download: boolean; upload: boolean };
  tests: { download: SpeedTest | null; upload: SpeedTest | null };
  timed_seconds?: number;
  plans?: string[];
}

const KINDS: Kind[] = ["download", "upload"];
const path = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/acs/speedtest`;
const KNOWN_ERRORS = ["Error_InitConnectionFailed", "Error_NoResponse", "Error_TransferFailed", "Error_IncorrectSize", "Error_Timeout"];

/** Download and upload speed test run by the CPE itself (TR-143, ACS-06); follows a running test every few seconds. */
export function AcsSpeedtest({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const key = ["device", deviceId, "acs-speedtest"];
  const query = useQuery({
    queryKey: key,
    queryFn: () => api<AcsSpeedtestData>(path(deviceId)),
    refetchInterval: (state) => (Object.values(state.state.data?.tests ?? {}).some((test) => test?.state === "requested") ? 4000 : false),
  });
  const run = useMutation({
    mutationFn: (kind: Kind) => api<AcsSpeedtestData>(`${path(deviceId)}/${kind}`, { method: "POST" }),
    onSuccess: (data) => client.setQueryData(key, data),
  });
  const data = query.data;
  if (!data || !data.linked) return null;
  const running = KINDS.some((kind) => data.tests[kind]?.state === "requested");
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("acsSpeedtest.title")}</h2>
          <p className="ui-muted">{t("acsSpeedtest.help", { size: data.size_mb })} {t("acsSpeedtest.timedHelp", { seconds: data.timed_seconds ?? 20 })}</p>
          {data.plans && data.plans.length > 0 && <p>{t("acsSpeedtest.plan", { plans: data.plans.join(", ") })}</p>}
        </div>
        {data.can_run && (
          <div className="ui-row-actions">
            {KINDS.map((kind) => (
              <button key={kind} type="button" className="ui-button" disabled={run.isPending || running} onClick={() => run.mutate(kind)}>
                {t(`acsSpeedtest.run.${kind}`)}
              </button>
            ))}
          </div>
        )}
      </div>
      {run.error && <div className="ui-alert ui-alert-danger">{(run.error as Error).message}</div>}
      <p className="ui-muted">{t("acsSpeedtest.burstNote")}</p>
      <div className="ui-metrics">
        {KINDS.map((kind) => {
          const test = data.tests[kind];
          const result = test?.result;
          return (
            <div key={kind} className="ui-metric">
              <span>{t(`acsSpeedtest.kinds.${kind}`)}</span>
              <strong>{test?.state === "complete" && (result?.sustained_mbps ?? result?.mbps) != null
                ? t("acsSpeedtest.mbps", { value: (result!.sustained_mbps ?? result!.mbps)!.toLocaleString(i18n.language) }) : "—"}</strong>
              {test?.state === "complete" && result?.sustained_mbps != null && (
                <small>{t("acsSpeedtest.breakdown", { sustained: result.sustained_mbps.toLocaleString(i18n.language),
                  average: (result.mbps ?? 0).toLocaleString(i18n.language), peak: (result.peak_mbps ?? 0).toLocaleString(i18n.language) })}</small>
              )}
              <small>
                {!test ? t("acsSpeedtest.never") : (
                  <>
                    {t(`acsSpeedtest.states.${test.state}`)}
                    {test.state === "requested" && !test.started ? ` · ${t("acsSpeedtest.waitingContact")}` : ""}
                    {" · "}{formatDateTime(test.completed_at ?? test.requested_at, i18n.language)}{test.by ? ` · ${test.by}` : ""}
                    {result?.seconds ? ` · ${t("acsSpeedtest.detail", { size: Math.round((result.bytes ?? 0) / 1048576), seconds: result.seconds })}` : ""}
                    {test.state === "complete" && result?.seconds != null && result.seconds < 8 && !result.sustained_mbps ? ` · ${t("acsSpeedtest.tooShort")}` : ""}
                    {" · "}{test.server === "custom" ? test.host : t("acsSpeedtest.toNsm")}
                  </>
                )}
              </small>
              {test?.state === "error" && result?.state && (
                <small className="ui-mono">
                  {result.state}{KNOWN_ERRORS.includes(result.state) ? ` · ${t(`acsSpeedtest.errors.${result.state}`)}` : ""}
                </small>
              )}
              {result?.short && <small>{t("acsSpeedtest.short", { bytes: result.bytes })}</small>}
              {test?.error && <small>{test.error}</small>}
            </div>
          );
        })}
      </div>
    </section>
  );
}
