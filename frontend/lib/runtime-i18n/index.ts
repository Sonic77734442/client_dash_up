import { ruAuthMap } from "./ru/auth";
import { ruCommonMap } from "./ru/common";
import { ruPagesMap } from "./ru/pages";
import { ruPlatformMap } from "./ru/platform";
import { ruSyncMonitorMap } from "./ru/sync-monitor";
import { ruWordMap } from "./ru/words";

export const runtimeRuMap: Record<string, string> = {
  ...ruCommonMap,
  ...ruAuthMap,
  ...ruPlatformMap,
  ...ruSyncMonitorMap,
  ...ruPagesMap,
};

export const runtimeRuToEnMap: Record<string, string> = Object.fromEntries(
  Object.entries(runtimeRuMap).map(([en, ru]) => [ru, en]),
);

export const runtimeRuWordMap = ruWordMap;
export const runtimeRuToEnWordMap: Record<string, string> = Object.fromEntries(
  Object.entries(ruWordMap).map(([en, ru]) => [ru, en]),
);
