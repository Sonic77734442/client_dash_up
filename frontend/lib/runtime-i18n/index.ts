import { ruAuthMap } from "./ru/auth";
import { ruCommonMap } from "./ru/common";
import { ruPlatformMap } from "./ru/platform";
import { ruSyncMonitorMap } from "./ru/sync-monitor";

export const runtimeRuMap: Record<string, string> = {
  ...ruCommonMap,
  ...ruAuthMap,
  ...ruPlatformMap,
  ...ruSyncMonitorMap,
};

export const runtimeRuToEnMap: Record<string, string> = Object.fromEntries(
  Object.entries(runtimeRuMap).map(([en, ru]) => [ru, en]),
);

