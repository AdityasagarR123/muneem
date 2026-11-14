import { createContext, ReactNode, useContext, useState } from "react";

export type Lang = "en" | "hinglish" | "hi";

const Ctx = createContext<{ lang: Lang; setLang: (l: Lang) => void }>({ lang: "hinglish", setLang: () => {} });

function read(): Lang {
  try {
    const v = localStorage.getItem("muneem.lang");
    if (v === "en" || v === "hinglish" || v === "hi") return v;
  } catch {
    /* storage unavailable */
  }
  return "hinglish";
}

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [lang, set] = useState<Lang>(read);
  const setLang = (l: Lang) => {
    set(l);
    try {
      localStorage.setItem("muneem.lang", l);
    } catch {
      /* ignore */
    }
  };
  return <Ctx.Provider value={{ lang, setLang }}>{children}</Ctx.Provider>;
}

export const usePrefs = () => useContext(Ctx);

/** English for "en", Hinglish/Hindi text otherwise (findings carry both). */
export const pick = (lang: Lang, en: string, hi: string) => (lang === "en" ? en : hi || en);
