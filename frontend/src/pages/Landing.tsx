import { ReactNode, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight, BadgeCheck, BellRing, ChevronDown, Database, FileSpreadsheet, Globe, Languages, LineChart,
  MessageCircle, MessagesSquare, Moon, Wallet, PackageCheck, PlugZap, ShieldCheck, Sparkles, Sun, Truck, Users,
} from "lucide-react";
import { Logo } from "../components/Icon";

/* ------------------------------------------------------------------ motion helpers */
const reduced = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Fades + lifts + un-blurs its children the first time they scroll into view. */
function Reveal({ children, delay = 0, className = "", as: Tag = "div" }: {
  children: ReactNode; delay?: number; className?: string; as?: any;
}) {
  const ref = useRef<HTMLElement>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || reduced()) { setShown(true); return; }
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setShown(true); io.disconnect(); } },
      { threshold: 0.15, rootMargin: "0px 0px -40px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return (
    <Tag ref={ref} className={`lp-reveal ${shown ? "is-in" : ""} ${className}`} style={{ transitionDelay: `${delay}ms` }}>
      {children}
    </Tag>
  );
}

/** Counts from 0 to `to` once visible. */
function CountUp({ to, decimals = 0, prefix = "", suffix = "" }: { to: number; decimals?: number; prefix?: string; suffix?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [v, setV] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (reduced()) { setV(to); return; }
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      io.disconnect();
      const t0 = performance.now(), dur = 1400;
      const tick = (t: number) => {
        const p = Math.min(1, (t - t0) / dur);
        setV(to * (1 - Math.pow(1 - p, 3)));
        if (p < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }, { threshold: 0.5 });
    io.observe(el);
    return () => io.disconnect();
  }, [to]);
  return <span ref={ref} className="tabular-nums">{prefix}{v.toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}{suffix}</span>;
}

/** Tilted product shot that flattens as the page scrolls. */
function useTilt() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || reduced()) return;
    let raf = 0;
    const on = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const r = el.getBoundingClientRect();
        const p = Math.min(1, Math.max(0, 1 - (r.top - 80) / (window.innerHeight * 0.75)));
        el.style.transform = `perspective(1400px) rotateX(${(1 - p) * 22}deg) scale(${0.9 + p * 0.1}) translateY(${(1 - p) * 30}px)`;
      });
    };
    on();
    window.addEventListener("scroll", on, { passive: true });
    window.addEventListener("resize", on);
    return () => { window.removeEventListener("scroll", on); window.removeEventListener("resize", on); cancelAnimationFrame(raf); };
  }, []);
  return ref;
}

/* ------------------------------------------------------------------ page */
export default function Landing() {
  const tilt = useTilt();
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 12);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  useEffect(() => { document.title = "Muneem - the always-on manager for your store"; }, []);

  return (
    <div className="lp min-h-screen overflow-x-hidden bg-canvas text-ink">
      <style>{CSS}</style>

      {/* nav */}
      <header className="fixed inset-x-0 top-3 z-50 px-4">
        <nav className={`mx-auto flex max-w-5xl items-center justify-between rounded-2xl border px-4 py-2.5 transition-all duration-500 ${scrolled ? "border-line bg-surface/85 shadow-[0_8px_30px_rgb(30_25_15/0.08)] backdrop-blur-md" : "border-transparent bg-surface/40 backdrop-blur-sm"}`}>
          <a href="#top" className="flex items-center gap-2 font-semibold"><Logo size={24} /> Muneem</a>
          <div className="hidden items-center gap-7 text-sm text-ink/70 md:flex">
            <a href="#how" className="hover:text-ink">How it works</a>
            <a href="#features" className="hover:text-ink">Features</a>
            <a href="#proof" className="hover:text-ink">Results</a>
            <a href="#faq" className="hover:text-ink">FAQ</a>
          </div>
          <Link to="/" className="lp-btn lp-btn-dark !py-2 text-sm">Open app <ArrowRight size={15} /></Link>
        </nav>
      </header>

      {/* hero */}
      <section id="top" className="relative px-4 pb-10 pt-36 md:pt-44">
        <div className="lp-sky" aria-hidden="true"><span /><span /><span /></div>
        <div className="relative mx-auto max-w-4xl text-center">
          <Reveal className="mx-auto inline-flex items-center gap-2 rounded-full border border-line bg-surface/80 py-1.5 pl-1.5 pr-4 text-sm shadow-sm backdrop-blur">
            <span className="grid h-6 w-6 place-items-center rounded-full bg-accent text-surface"><Sparkles size={13} /></span>
            Built for 24x7 stores · English, Hinglish, हिंदी
          </Reveal>
          <Reveal delay={90} as="h1" className="lp-title mt-6 text-[2.6rem] leading-[1.05] sm:text-6xl md:text-7xl">
            The manager that never<br className="hidden sm:block" /> clocks out
          </Reveal>
          <Reveal delay={180} as="p" className="mx-auto mt-6 max-w-2xl text-base text-ink/70 md:text-lg">
            Muneem watches your sales, stock, shifts and customers around the clock - and tells you what to
            reorder, who to call back and why today was slow. Every number is calculated from your own records
            and checked before you see it.
          </Reveal>
          <Reveal delay={270} className="mt-9 flex flex-wrap items-center justify-center gap-3">
            <Link to="/" className="lp-btn lp-btn-accent">Open Muneem <ArrowRight size={16} /></Link>
            <a href="#how" className="lp-btn lp-btn-ghost">See how it works</a>
          </Reveal>
        </div>

        {/* product shot */}
        <Reveal delay={350} className="relative mx-auto mt-16 max-w-5xl">
          <div ref={tilt} className="lp-tilt"><ProductShot /></div>
          <p className="mt-4 text-center text-xs text-muted">Illustration of the app with sample-shop data</p>
        </Reveal>
      </section>

      {/* sources marquee */}
      <section className="py-14">
        <Reveal as="p" className="text-center text-sm font-medium text-muted">Works with the records you already keep</Reveal>
        <div className="lp-marquee mt-6">
          <div className="lp-marquee-track">
            {[0, 1].map((k) => (
              <div key={k} className="flex shrink-0 gap-4 pr-4" aria-hidden={k === 1}>
                {SOURCES.map(([Icon, label]) => (
                  <span key={label} className="flex items-center gap-2 whitespace-nowrap rounded-xl border border-line bg-surface px-5 py-3 text-sm font-medium text-ink/80">
                    <Icon size={17} className="text-accent" /> {label}
                  </span>
                ))}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* how it works */}
      <section id="how" className="px-4 py-20">
        <div className="mx-auto max-w-5xl">
          <SectionHead kicker="How it works" title="From messy records to a clear next step"
            sub="Three steps, no training. Muneem reads your data, you ask in your own words, and it hands you a draft to act on." />
          <div className="mt-16 space-y-24">
            {STEPS.map((s, i) => (
              <div key={s.title} className={`grid items-center gap-10 md:grid-cols-2 ${i % 2 ? "md:[&>*:first-child]:order-2" : ""}`}>
                <Reveal>
                  <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-xs font-semibold text-accent">
                    Step {i + 1} · {s.tag}</span>
                  <h3 className="lp-title mt-4 text-3xl md:text-4xl">{s.title}</h3>
                  <p className="mt-4 text-ink/70">{s.body}</p>
                  <ul className="mt-5 space-y-2 text-sm">
                    {s.points.map((p) => <li key={p} className="flex gap-2"><BadgeCheck size={17} className="mt-0.5 shrink-0 text-accent" />{p}</li>)}
                  </ul>
                </Reveal>
                <Reveal delay={120}><div className="lp-card p-5">{s.visual}</div></Reveal>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* bento features */}
      <section id="features" className="px-4 py-20">
        <div className="mx-auto max-w-5xl">
          <SectionHead kicker="Features" title="Everything a night-shift manager wishes they had"
            sub="One place for the numbers, the alerts and the messages - with nothing made up." />
          <div className="mt-14 grid gap-4 md:grid-cols-6">
            {FEATURES.map((f, i) => (
              <Reveal key={f.title} delay={(i % 3) * 90} className={`lp-card lp-hover p-6 ${f.wide ? "md:col-span-4" : "md:col-span-2"}`}>
                <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent-soft text-accent"><f.icon size={19} /></span>
                <h3 className="mt-4 text-lg font-semibold">{f.title}</h3>
                <p className="mt-1.5 text-sm text-ink/70">{f.body}</p>
                {f.extra}
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* proof */}
      <section id="proof" className="px-4 py-20">
        <div className="mx-auto max-w-5xl">
          <Reveal className="lp-card overflow-hidden md:grid md:grid-cols-[1.1fr_1fr]">
            <div className="p-8 md:p-10">
              <span className="text-xs font-semibold uppercase tracking-wider text-accent">Sample shop result</span>
              <h3 className="lp-title mt-3 text-3xl">A win-back message that measurably worked</h3>
              <p className="mt-4 text-ink/70">
                Muneem spotted regulars who had stopped coming, drafted a personal WhatsApp note for each, and kept a
                random quarter as a hold-out group that got nothing - so the result is a real comparison, not a guess.
              </p>
              <p className="mt-4 text-xs text-muted">Figures from the built-in synthetic sample shop, not a customer case study.</p>
            </div>
            <div className="grid grid-cols-2 gap-px border-t border-line bg-line md:border-l md:border-t-0">
              {[[84.2, 1, "", "%", "came back after the message"], [55, 0, "", "%", "came back without it"],
                [29208, 0, "₹", "", "extra sales from the campaign"], [14, 0, "", "", "regulars flagged as slipping away"]].map(([n, d, p, s, l]) => (
                <div key={l as string} className="bg-surface p-6">
                  <div className="lp-title text-3xl text-ink"><CountUp to={n as number} decimals={d as number} prefix={p as string} suffix={s as string} /></div>
                  <div className="mt-1 text-sm text-ink/70">{l}</div>
                </div>
              ))}
            </div>
          </Reveal>
        </div>
      </section>

      {/* faq */}
      <section id="faq" className="px-4 py-20">
        <div className="mx-auto max-w-3xl">
          <SectionHead kicker="FAQ" title="Questions owners ask us" />
          <div className="mt-12 space-y-3">
            {FAQ.map(([q, a], i) => <Faq key={q} q={q} a={a} delay={i * 60} />)}
          </div>
        </div>
      </section>

      {/* final cta */}
      <section className="px-4 pb-16 pt-10">
        <Reveal className="lp-cta relative mx-auto max-w-5xl overflow-hidden rounded-3xl px-6 py-16 text-center md:py-20">
          <div className="lp-sky lp-sky-dark" aria-hidden="true"><span /><span /><span /></div>
          <div className="relative">
            <h2 className="lp-title text-4xl text-surface md:text-5xl">Hisaab bhi, salaah bhi.</h2>
            <p className="mx-auto mt-4 max-w-xl text-surface/75">Load the sample store in one click, or connect a Google Sheet and see your own numbers in minutes.</p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Link to="/" className="lp-btn bg-surface text-ink hover:bg-surface/90">Open Muneem <ArrowRight size={16} /></Link>
              <Link to="/data" className="lp-btn border border-surface/30 text-surface hover:bg-surface/10">Connect your data</Link>
            </div>
          </div>
        </Reveal>
        <footer className="mx-auto mt-10 flex max-w-5xl flex-wrap items-center justify-between gap-3 text-sm text-muted">
          <span className="flex items-center gap-2"><Logo size={20} /> Muneem · hisaab bhi, salaah bhi</span>
          <span>Muneem drafts; you decide and send. Nothing is sent on your behalf.</span>
        </footer>
      </section>
    </div>
  );
}

function SectionHead({ kicker, title, sub }: { kicker: string; title: string; sub?: string }) {
  return (
    <div className="mx-auto max-w-2xl text-center">
      <Reveal as="span" className="inline-block rounded-full border border-line bg-surface px-3 py-1 text-xs font-semibold uppercase tracking-wider text-accent">{kicker}</Reveal>
      <Reveal as="h2" delay={80} className="lp-title mt-4 text-3xl md:text-5xl">{title}</Reveal>
      {sub && <Reveal as="p" delay={160} className="mt-4 text-ink/70">{sub}</Reveal>}
    </div>
  );
}

function Faq({ q, a, delay }: { q: string; a: string; delay: number }) {
  const [open, setOpen] = useState(false);
  return (
    <Reveal delay={delay} className="lp-card">
      <button className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left font-medium" aria-expanded={open} onClick={() => setOpen(!open)}>
        {q}<ChevronDown size={18} className={`shrink-0 text-muted transition-transform duration-300 ${open ? "rotate-180" : ""}`} />
      </button>
      <div className={`grid transition-all duration-300 ease-out ${open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"}`}>
        <p className="overflow-hidden px-5 text-sm text-ink/70"><span className="block pb-4">{a}</span></p>
      </div>
    </Reveal>
  );
}

/* ------------------------------------------------------------------ visuals (pure HTML, no images) */
function ProductShot() {
  const bars = [38, 52, 44, 61, 58, 72, 66, 80, 74, 88, 70, 92];
  return (
    <div className="rounded-[22px] border border-line bg-surface/90 p-3 shadow-[0_30px_80px_-20px_rgb(30_25_15/0.25)] backdrop-blur">
      <div className="flex items-center gap-1.5 px-2 pb-3">
        <span className="h-2.5 w-2.5 rounded-full bg-[#e0a090]" /><span className="h-2.5 w-2.5 rounded-full bg-[#e5c97a]" /><span className="h-2.5 w-2.5 rounded-full bg-[#9cc4a5]" />
        <span className="ml-3 text-xs text-muted">Muneem · Night shift</span>
      </div>
      <div className="grid gap-3 rounded-2xl bg-canvas p-3 md:grid-cols-[1.6fr_1fr]">
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[["Sales · 30 days", "₹9,78,950", "+7.1%"], ["Margin after GST", "₹1,08,344", "+11.9%"], ["Customers at risk", "14", "₹54k/mo"], ["Night sales", "18%", "10pm–6am"]].map(([k, v, s]) => (
              <div key={k} className="rounded-xl border border-line bg-surface p-3 text-left">
                <div className="text-[10px] text-muted">{k}</div><div className="mt-1 font-semibold tabular-nums">{v}</div>
                <div className="text-[10px] text-accent">{s}</div>
              </div>
            ))}
          </div>
          <div className="rounded-xl border border-line bg-surface p-4 text-left">
            <div className="flex items-center justify-between text-xs"><b>Sales by hour</b><span className="text-muted">today vs usual</span></div>
            <div className="mt-4 flex h-28 items-end gap-1.5">
              {bars.map((h, i) => <span key={i} className="lp-bar flex-1 rounded-t-md bg-accent/80" style={{ height: `${h}%`, animationDelay: `${600 + i * 60}ms` }} />)}
            </div>
          </div>
        </div>
        <div className="space-y-3 text-left">
          <div className="rounded-xl border border-line bg-surface p-3 text-xs">
            <div className="flex items-center gap-2 font-semibold"><BellRing size={14} className="text-[#c2553f]" /> Runs out before delivery</div>
            <p className="mt-1.5 text-ink/70">Toor Dal 1kg - 1.2 days left, supplier needs 2. About ₹3,877 of sales at risk.</p>
            <span className="mt-2 inline-block rounded-md bg-ink px-2 py-1 text-[10px] text-surface">Make restock list →</span>
          </div>
          <div className="rounded-xl border border-line bg-surface p-3 text-xs">
            <div className="ml-auto w-fit rounded-lg bg-surface-2 px-2.5 py-1.5">Kal raat sale kam kyun hui?</div>
            <div className="mt-2 rounded-lg bg-accent-soft px-2.5 py-1.5 text-ink/80">Raat 11 ke baad 2 bestseller khatam the - lagbhag ₹4,120 ki bikri gayi.</div>
            <div className="mt-2 text-[10px] text-accent">✓ 2 numbers verified</div>
          </div>
        </div>
      </div>
    </div>
  );
}

const SOURCES: [typeof Database, string][] = [
  [FileSpreadsheet, "Google Sheets · live"], [FileSpreadsheet, "Excel & CSV"], [Database, "PostgreSQL"], [Database, "MySQL"],
  [PlugZap, "Billing app webhooks"], [MessageCircle, "WhatsApp drafts"], [Globe, "Web market prices"], [Languages, "Hinglish & हिंदी"],
];

const STEPS = [
  { tag: "Connect", title: "Bring the records you already have",
    body: "Upload a billing export, link a Google Sheet or a read-only database. Muneem works out which column is what and asks only when it is unsure.",
    points: ["Live Google Sheets, re-read every 2 minutes", "Remembers each layout, so the next export maps itself", "A broken edit never wipes your data"],
    visual: (
      <div className="space-y-2 text-sm">
        {[["Bill No.", "Invoice", "sure"], ["Bill Dt", "Date", "sure"], ["Rate (incl. GST)", "Price incl. GST", "sure"], ["Remarks", "-", "skipped"]].map(([a, b, s]) => (
          <div key={a} className="flex items-center justify-between rounded-lg border border-line bg-canvas px-3 py-2">
            <span className="font-mono text-xs">{a}</span><ArrowRight size={14} className="text-muted" />
            <span className="w-32">{b}</span>
            <span className={`rounded px-1.5 py-0.5 text-[11px] ${s === "sure" ? "bg-accent-soft text-accent" : "bg-surface-2 text-muted"}`}>{s}</span>
          </div>
        ))}
      </div>
    ) },
  { tag: "Ask", title: "Ask like you would ask your munim",
    body: "\"Why was last night slow?\", \"Kya order karun?\", \"Which regulars stopped coming?\" - answers come with the calculation behind them and a chart.",
    points: ["Every number traced back to your records", "Web facts shown separately, with source and date", "Works offline in rules mode if the AI is down"],
    visual: (
      <div className="space-y-2 text-sm">
        <div className="ml-auto w-fit rounded-xl bg-surface-2 px-3 py-2">Is hafte sale kyun giri?</div>
        <div className="rounded-xl bg-accent-soft px-3 py-2">Pichhle hafte Diwali ki bheed thi. Bill 18% kam, average bill lagbhag same - zyada farak festival ka hai.</div>
        <div className="flex gap-2 text-[11px]"><span className="rounded bg-surface px-2 py-1 ring-1 ring-line">✓ 3 numbers verified</span><span className="rounded bg-surface px-2 py-1 ring-1 ring-line">Show calculation</span></div>
      </div>
    ) },
  { tag: "Act", title: "Get a draft, not a to-do list",
    body: "Restock orders per supplier, win-back messages per customer, udhaar reminders - ready to review and send from WhatsApp. You stay in charge.",
    points: ["Order progress from draft to shelf", "Campaigns measured against a hold-out group", "Muneem never sends anything by itself"],
    visual: (
      <div className="flex items-center justify-between gap-2 text-xs">
        {[[MessagesSquare, "Drafted"], [Truck, "Sent"], [PackageCheck, "Delivered"], [LineChart, "Stock up"]].map(([I, l], i) => {
          const Ic = I as typeof Truck;
          return (
            <div key={l as string} className="flex flex-1 flex-col items-center gap-2">
              <span className={`lp-cube grid h-14 w-14 place-items-center rounded-2xl ${i < 2 ? "bg-accent-soft text-accent" : "bg-surface-2 text-muted"}`} style={{ animationDelay: `${i * 200}ms` }}><Ic size={20} /></span>
              <span className="font-medium">{l as string}</span>
            </div>
          );
        })}
      </div>
    ) },
];

const FEATURES = [
  { icon: Moon, title: "Round-the-clock view", wide: true, body: "See how each shift and each hour is doing - which items run out at night and when the rush starts.",
    extra: (
      <div className="mt-5 flex items-end gap-1">
        {Array.from({ length: 24 }, (_, h) => {
          const v = 25 + 60 * Math.max(0, Math.sin(((h - 6) / 24) * Math.PI * 2)) + (h >= 18 && h <= 21 ? 25 : 0);
          return <span key={h} title={`${h}:00`} className={`lp-bar flex-1 rounded-t ${h < 6 || h >= 22 ? "bg-ink/70" : "bg-accent/80"}`} style={{ height: v, animationDelay: `${h * 30}ms` }} />;
        })}
        <span className="ml-2 flex flex-col gap-1 text-[10px] text-muted"><span className="flex items-center gap-1"><Sun size={11} /> day</span><span className="flex items-center gap-1"><Moon size={11} /> night</span></span>
      </div>
    ) },
  { icon: ShieldCheck, title: "Every number checked", body: "The AI writes the sentence; the numbers come from code. Anything that doesn't match your data is flagged." },
  { icon: Truck, title: "Restock before it runs out", body: "Festival-aware reorder quantities, pack-rounded, grouped by supplier, with a WhatsApp draft." },
  { icon: Users, title: "Win back regulars", body: "Spot customers drifting away from their usual rhythm and message them personally." },
  { icon: Globe, title: "Market context, cited", body: "Ask about wholesale prices or GST changes - answers come with the site and date, kept apart from your numbers." },
  { icon: FileSpreadsheet, title: "Live Google Sheets", wide: true, body: "Keep using the sheet your staff already fill in. Muneem picks up changes every couple of minutes and checks them before they count.",
    extra: (
      <div className="mt-5 flex flex-wrap gap-2 text-xs">
        {["Daily bills · 2 min ago", "Stock count · 5 min ago", "Expenses · just now"].map((t, i) => (
          <span key={t} className="lp-pop flex items-center gap-1.5 rounded-full border border-line bg-canvas px-3 py-1.5" style={{ animationDelay: `${i * 160}ms` }}>
            <span className="h-1.5 w-1.5 rounded-full bg-accent" /> {t}</span>
        ))}
      </div>
    ) },
  { icon: Wallet, title: "Udhaar that gets paid", body: "A running ledger for every khata customer, oldest dues first, with a polite reminder ready to send." },
];

const FAQ: [string, string][] = [
  ["Does Muneem send messages or place orders by itself?", "No. It drafts restock orders, customer messages and reminders; you review them and send from your own WhatsApp. Nothing leaves without you."],
  ["Can I trust the numbers?", "Sales, margins, stock days and every rupee figure are calculated in code from your records. The AI only phrases the answer, and any number it writes that is not in the calculation is flagged."],
  ["Which languages does it speak?", "English, Hinglish (Hindi in Roman letters) and Hindi in Devanagari - it replies in the language you ask in."],
  ["How do I connect my data?", "Upload a CSV or Excel export, link a Google Sheet shared as 'anyone with the link can view', or connect a read-only PostgreSQL or MySQL database."],
  ["What happens to my customers' details when it searches the web?", "Before any web search, names, phone numbers, UPI ids, GSTINs and amounts are removed from the query. Web results are always shown separately with their source."],
];

const CSS = `
.lp{scroll-behavior:smooth}
html:has(.lp){scroll-behavior:smooth}
.lp-title{font-family:"Fraunces",Georgia,serif;font-weight:600;letter-spacing:-0.02em}
.lp-reveal{opacity:0;transform:translateY(28px);filter:blur(8px);transition:opacity .9s cubic-bezier(.2,.7,.2,1),transform .9s cubic-bezier(.2,.7,.2,1),filter .9s ease}
.lp-reveal.is-in{opacity:1;transform:none;filter:none}
.lp-btn{display:inline-flex;align-items:center;gap:.4rem;border-radius:999px;padding:.8rem 1.4rem;font-weight:600;font-size:.95rem;transition:transform .25s ease,box-shadow .25s ease,background .25s}
.lp-btn:hover{transform:translateY(-2px)}
.lp-btn-dark{background:rgb(var(--ink));color:rgb(var(--surface))}
.lp-btn-accent{background:rgb(var(--accent));color:rgb(var(--surface));box-shadow:0 10px 30px -10px rgb(var(--accent)/.7),inset 0 1px 0 rgb(255 255 255/.2)}
.lp-btn-accent:hover{box-shadow:0 16px 36px -10px rgb(var(--accent)/.8)}
.lp-btn-ghost{background:rgb(var(--surface)/.8);border:1px solid rgb(var(--line));backdrop-filter:blur(6px)}
.lp-card{background:rgb(var(--surface));border:1px solid rgb(var(--line));border-radius:20px;box-shadow:0 1px 2px rgb(30 25 15/.04)}
.lp-hover{transition:transform .35s ease,box-shadow .35s ease,opacity .9s cubic-bezier(.2,.7,.2,1),filter .9s}
.lp-hover.is-in:hover{transform:translateY(-4px);box-shadow:0 18px 40px -18px rgb(30 25 15/.25)}
.lp-sky{position:absolute;inset:-10% 0 auto 0;height:760px;overflow:hidden;pointer-events:none;-webkit-mask-image:linear-gradient(to bottom,#000 55%,transparent);mask-image:linear-gradient(to bottom,#000 55%,transparent)}
.lp-sky span{position:absolute;border-radius:50%;filter:blur(70px);opacity:.7;animation:lp-drift 18s ease-in-out infinite alternate}
.lp-sky span:nth-child(1){width:520px;height:420px;left:-120px;top:60px;background:#cfe3d5}
.lp-sky span:nth-child(2){width:560px;height:460px;right:-140px;top:0;background:#f1dcc4;animation-delay:-6s}
.lp-sky span:nth-child(3){width:420px;height:360px;left:35%;top:260px;background:#dfe0ef;animation-delay:-12s}
.lp-sky-dark{inset:0;height:auto;-webkit-mask-image:none;mask-image:none}
.lp-sky-dark span{opacity:.35}
.lp-cta{background:rgb(var(--ink))}
@keyframes lp-drift{to{transform:translate(60px,40px) scale(1.12)}}
.lp-tilt{transform-origin:50% 0;transition:transform .12s linear;will-change:transform}
.lp-marquee{overflow:hidden;-webkit-mask-image:linear-gradient(90deg,transparent,#000 12%,#000 88%,transparent);mask-image:linear-gradient(90deg,transparent,#000 12%,#000 88%,transparent)}
.lp-marquee-track{display:flex;width:max-content;animation:lp-scroll 32s linear infinite}
.lp-marquee:hover .lp-marquee-track{animation-play-state:paused}
@keyframes lp-scroll{to{transform:translateX(-50%)}}
.lp-bar{transform-origin:bottom;animation:lp-grow .9s cubic-bezier(.2,.7,.2,1) both}
@keyframes lp-grow{from{transform:scaleY(0)}}
.lp-cube{animation:lp-float 3.6s ease-in-out infinite}
@keyframes lp-float{50%{transform:translateY(-6px)}}
.lp-pop{animation:lp-pop .6s cubic-bezier(.2,.9,.3,1.3) both}
@keyframes lp-pop{from{opacity:0;transform:scale(.8) translateY(8px)}}
@media (prefers-reduced-motion:reduce){.lp *{animation:none!important;transition:none!important}.lp-reveal{opacity:1;transform:none;filter:none}}
`;
