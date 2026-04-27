"use client";

import { useCallback, useEffect, useState } from "react";
import { Locale } from "../lib/i18n";

const LS_LOCALE = "ops_locale";
const LOCALE_UPDATED_EVENT = "ops-locale-updated";

function normalizeLocale(value: string | null | undefined): Locale {
  return value === "ru" ? "ru" : "en";
}

export function useLocale() {
  const [locale, setLocaleState] = useState<Locale>("en");

  useEffect(() => {
    const saved = normalizeLocale(localStorage.getItem(LS_LOCALE));
    setLocaleState(saved);
    document.documentElement.lang = saved;
    const onStorage = () => {
      const next = normalizeLocale(localStorage.getItem(LS_LOCALE));
      setLocaleState(next);
      document.documentElement.lang = next;
    };
    const onUpdated = () => onStorage();
    window.addEventListener("storage", onStorage);
    window.addEventListener(LOCALE_UPDATED_EVENT, onUpdated);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(LOCALE_UPDATED_EVENT, onUpdated);
    };
  }, []);

  const setLocale = useCallback((next: Locale) => {
    const val = normalizeLocale(next);
    localStorage.setItem(LS_LOCALE, val);
    setLocaleState(val);
    document.documentElement.lang = val;
    window.dispatchEvent(new Event(LOCALE_UPDATED_EVENT));
  }, []);

  return { locale, setLocale };
}
