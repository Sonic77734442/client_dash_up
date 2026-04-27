"use client";

import { useEffect } from "react";
import { useLocale } from "../hooks/useLocale";
import {
  runtimeRuMap,
  runtimeRuToEnMap,
  runtimeRuToEnWordMap,
  runtimeRuWordMap,
} from "../lib/runtime-i18n";

const ATTRS = ["placeholder", "title", "aria-label"] as const;

const runtimeRuWordMapLower: Record<string, string> = Object.fromEntries(
  Object.entries(runtimeRuWordMap).map(([en, ru]) => [en.toLowerCase(), ru]),
);
const runtimeRuToEnWordMapLower: Record<string, string> = Object.fromEntries(
  Object.entries(runtimeRuToEnWordMap).map(([ru, en]) => [ru.toLowerCase(), en]),
);

function applyCase(source: string, translated: string): string {
  if (!source) return translated;
  const hasLetters = /[A-Za-z\u0400-\u04FF]/.test(source);
  if (!hasLetters) return translated;
  const isUpper = source === source.toUpperCase() && source !== source.toLowerCase();
  if (isUpper) return translated.toUpperCase();
  const isCapitalized = source[0] === source[0].toUpperCase() && source.slice(1) === source.slice(1).toLowerCase();
  if (isCapitalized) return translated.charAt(0).toUpperCase() + translated.slice(1);
  return translated;
}

function translateToken(token: string, ru: boolean): string {
  const direct = ru ? runtimeRuWordMap[token] : runtimeRuToEnWordMap[token];
  if (direct) return direct;
  const lower = token.toLowerCase();
  const fallback = ru ? runtimeRuWordMapLower[lower] : runtimeRuToEnWordMapLower[lower];
  if (!fallback) return token;
  return applyCase(token, fallback);
}

function translateValue(value: string, ru: boolean): string {
  const trimmed = value.trim();
  if (!trimmed) return value;
  const exact = ru
    ? (runtimeRuMap[trimmed] || trimmed)
    : (runtimeRuToEnMap[trimmed] || trimmed);
  let translated = exact;
  if (translated === trimmed) {
    const normalized = trimmed.replaceAll("_", " ");
    translated = normalized.replace(/\p{L}[\p{L}\p{N}]*/gu, (token) => translateToken(token, ru));
    if (!translated) translated = trimmed;
  }
  if (translated === trimmed) return value;
  const leading = value.match(/^\s*/)?.[0] || "";
  const trailing = value.match(/\s*$/)?.[0] || "";
  return `${leading}${translated}${trailing}`;
}

export function RuntimeI18n() {
  const { locale } = useLocale();

  useEffect(() => {
    if (typeof document === "undefined") return;
    const ru = locale === "ru";
    let applying = false;

    const applyTranslations = () => {
      if (applying) return;
      applying = true;
      try {
        const root = document.body;
        if (!root) return;

        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let node = walker.nextNode();
        while (node) {
          const parentTag = (node.parentElement?.tagName || "").toUpperCase();
          if (parentTag === "SCRIPT" || parentTag === "STYLE" || parentTag === "NOSCRIPT") {
            node = walker.nextNode();
            continue;
          }
          const current = node.nodeValue || "";
          const next = translateValue(current, ru);
          if (next !== current) node.nodeValue = next;
          node = walker.nextNode();
        }

        const elements = root.querySelectorAll<HTMLElement>("*");
        for (const el of elements) {
          for (const attr of ATTRS) {
            const current = el.getAttribute(attr);
            if (!current) continue;
            const next = translateValue(current, ru);
            if (next !== current) el.setAttribute(attr, next);
          }
        }
      } finally {
        applying = false;
      }
    };

    applyTranslations();
    const observer = new MutationObserver(() => applyTranslations());
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: [...ATTRS],
    });
    return () => observer.disconnect();
  }, [locale]);

  return null;
}
