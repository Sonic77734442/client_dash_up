"use client";

import { useEffect } from "react";
import { useLocale } from "../hooks/useLocale";
import { runtimeRuMap, runtimeRuToEnMap } from "../lib/runtime-i18n";

const ATTRS = ["placeholder", "title", "aria-label"] as const;

function translateValue(value: string, ru: boolean): string {
  const trimmed = value.trim();
  if (!trimmed) return value;
  const translated = ru
    ? (runtimeRuMap[trimmed] || trimmed)
    : (runtimeRuToEnMap[trimmed] || trimmed);
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

