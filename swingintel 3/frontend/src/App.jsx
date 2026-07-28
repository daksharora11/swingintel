import React, { useState, useEffect, useMemo, useCallback } from "react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  BarChart,
  Bar,
  Cell,
} from "recharts";
import {
  TrendingUp,
  TrendingDown,
  Minus,
  Plus,
  X,
  Landmark,
  Newspaper,
  Building2,
  Globe2,
  Globe,
  Vote,
  Gavel,
  Info,
  RadioTower,
  Activity,
  Gauge as GaugeIcon,
  AlertTriangle,
  ArrowRightLeft,
  ChevronRight,
  RefreshCw,
  WifiOff,
  Wifi,
} from "lucide-react";

// ---------------------------------------------------------------------------
// SwingIntel — live frontend
//
// Prices: polled from the FastAPI backend every 8s (real, via yfinance).
// News: fetched on demand per ticker via a Refresh button, NOT on an
//   interval — NewsAPI's free tier is ~100 requests/day, an interval would
//   burn through that in minutes. Falls back to the mock articles until
//   you refresh at least once.
// Markets: still curated mock data. Real Polymarket markets don't come
//   tagged with "which ticker does this affect" or grouped into the
//   categories shown here — that mapping is a curation task, not a fetch,
//   so it's left as the next real step rather than faked as live.
// ---------------------------------------------------------------------------

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function sentimentColor(score) {
  if (score > 0.15) return "#4ADE80";
  if (score < -0.15) return "#F87171";
  return "#94A3B8";
}
function sentimentLabel(score) {
  if (score > 0.15) return "Bullish";
  if (score < -0.15) return "Bearish";
  return "Neutral";
}
function pct(v) {
  return `${Math.round(v * 100)}%`;
}

// ---- Sentiment tab data (mock fallback, replaced per-ticker on refresh) --

const TAG_META = {
  Earnings: { icon: Building2, color: "#5EEAD4" },
  Political: { icon: Landmark, color: "#FBBF24" },
  Macro: { icon: Globe2, color: "#818CF8" },
  Sector: { icon: RadioTower, color: "#F472B6" },
  Live: { icon: Wifi, color: "#5EEAD4" },
};

const POS_WORDS = ["beats", "surges", "raises", "approval", "expands", "record", "strong", "upgrade", "wins", "growth", "rally", "boosts"];
const NEG_WORDS = ["misses", "cuts", "probe", "tariff", "delays", "recall", "downgrade", "lawsuit", "slump", "warns", "halts", "shortfall"];

function scoreHeadline(text) {
  const lower = text.toLowerCase();
  let score = 0;
  POS_WORDS.forEach((w) => { if (lower.includes(w)) score += 1; });
  NEG_WORDS.forEach((w) => { if (lower.includes(w)) score -= 1; });
  const magnitude = Math.min(1, Math.abs(score) / 2.5 + 0.15);
  return { score: Math.max(-1, Math.min(1, score / 2)), magnitude };
}

const MOCK_ARTICLES = [
  { ticker: "NVDA", tag: "Earnings", source: "Reuters", time: "12m ago", headline: "NVDA beats Q2 estimates as data-center demand surges past forecasts" },
  { ticker: "NVDA", tag: "Political", source: "Bloomberg", time: "1h ago", headline: "New export tariff talks target advanced chipmakers, NVDA in scope" },
  { ticker: "NVDA", tag: "Sector", source: "CNBC", time: "3h ago", headline: "Semiconductor sector rally broadens as AI capex guidance raises across peers" },
  { ticker: "TSLA", tag: "Macro", source: "WSJ", time: "20m ago", headline: "Fed signals rate cuts could boost auto financing demand into Q4" },
  { ticker: "TSLA", tag: "Earnings", source: "Reuters", time: "2h ago", headline: "TSLA delivery numbers miss street expectations, margins under pressure" },
  { ticker: "TSLA", tag: "Political", source: "Politico", time: "5h ago", headline: "EV tax credit rollback proposal advances in committee, warns industry group" },
  { ticker: "AAPL", tag: "Sector", source: "Bloomberg", time: "40m ago", headline: "Consumer hardware names upgrade on stronger holiday sell-through outlook" },
  { ticker: "AAPL", tag: "Political", source: "FT", time: "4h ago", headline: "EU regulator opens probe into App Store terms, AAPL shares dip" },
  { ticker: "XOM", tag: "Macro", source: "Reuters", time: "1h ago", headline: "Oil prices rally on supply cut extension, energy majors set to benefit" },
  { ticker: "XOM", tag: "Political", source: "AP", time: "6h ago", headline: "Proposed drilling permit expansion clears key procedural vote" },
  { ticker: "JPM", tag: "Macro", source: "Bloomberg", time: "50m ago", headline: "Bank stocks climb as yield curve steepens on rate-cut repricing" },
  { ticker: "JPM", tag: "Earnings", source: "Reuters", time: "3h ago", headline: "JPM raises full-year net interest income guidance" },
].map((a, i) => ({ id: `mock-${i}`, ...a, ...scoreHeadline(a.headline) }));

const TICKERS = ["NVDA", "TSLA", "AAPL", "XOM", "JPM"];
const BASE_PRICE = { NVDA: 128.4, TSLA: 248.7, AAPL: 231.5, XOM: 118.2, JPM: 214.6 };

function buildHistory(seed) {
  let v = seed;
  return Array.from({ length: 14 }, (_, i) => {
    v += Math.sin(i * 1.3 + seed * 4) * 0.18 + (Math.random() - 0.5) * 0.12;
    v = Math.max(-1, Math.min(1, v));
    return { day: `D${i + 1}`, sentiment: Number(v.toFixed(2)) };
  });
}
const INITIAL_HISTORY = Object.fromEntries(TICKERS.map((t, i) => [t, buildHistory(((i + 1) * 0.37) % 1 - 0.3)]));

const SECTORS = [
  { name: "Semiconductors", score: 0.62 },
  { name: "Energy", score: 0.41 },
  { name: "Financials", score: 0.35 },
  { name: "EVs & Auto", score: -0.28 },
  { name: "Consumer Hardware", score: -0.08 },
];

// ---- Prediction Markets tab data (curated demo — see header note) --------

const CATEGORY_META = {
  "Fed / Rates": { icon: Landmark, color: "#5EEAD4" },
  Election: { icon: Vote, color: "#818CF8" },
  Regulatory: { icon: Gavel, color: "#FBBF24" },
  Geopolitical: { icon: Globe, color: "#F472B6" },
};

const MARKETS = [
  { id: "fed-cut-sept", category: "Fed / Rates", question: "Fed cuts rates at September meeting?", yes: 0.78, no: 0.21, volume: "$4.2M", change24h: 0.04, impacts: [{ ticker: "JPM", note: "Rate cuts compress bank net interest margins" }, { ticker: "Sector: Financials", note: "Broad tailwind for rate-sensitive names" }], group: "fed-sept" },
  { id: "fed-cut-50bps-sept", category: "Fed / Rates", question: "Fed cuts rates by 50bps+ at September meeting?", yes: 0.81, no: 0.18, volume: "$1.1M", change24h: 0.11, impacts: [{ ticker: "JPM", note: "Deeper cut, sharper margin pressure" }], group: "fed-sept", nestedUnder: "fed-cut-sept" },
  { id: "chip-tariff", category: "Regulatory", question: "New semiconductor export tariff enacted by year-end?", yes: 0.34, no: 0.65, volume: "$2.8M", change24h: -0.06, impacts: [{ ticker: "NVDA", note: "Direct exposure — advanced chip export scope" }, { ticker: "Sector: Semiconductors", note: "Sector-wide overhang if enacted" }], group: "tariff" },
  { id: "ev-credit-rollback", category: "Regulatory", question: "EV tax credit rollback passes committee vote?", yes: 0.58, no: 0.43, volume: "$640K", change24h: 0.09, impacts: [{ ticker: "TSLA", note: "Demand headwind if credit is cut" }], group: "ev" },
  { id: "eu-appstore-fine", category: "Regulatory", question: "EU issues formal App Store antitrust fine this quarter?", yes: 0.29, no: 0.72, volume: "$980K", change24h: 0.02, impacts: [{ ticker: "AAPL", note: "Fine + forced terms change if it lands" }], group: "appstore" },
  { id: "opec-extension", category: "Geopolitical", question: "OPEC+ extends supply cuts through Q1 next year?", yes: 0.67, no: 0.33, volume: "$1.9M", change24h: 0.05, impacts: [{ ticker: "XOM", note: "Supports crude price floor" }], group: "opec" },
];

function detectArbitrage(markets) {
  const flags = [];
  markets.forEach((m) => {
    const sum = m.yes + m.no;
    if (Math.abs(sum - 1) > 0.03) {
      flags.push({
        type: sum < 1 ? "Risk-free" : "Overpriced",
        market: m,
        detail: sum < 1
          ? `YES + NO = $${sum.toFixed(2)} — buying both sides locks in $${(1 - sum).toFixed(2)} per share regardless of outcome.`
          : `YES + NO = $${sum.toFixed(2)} — combined price exceeds $1.00, the pair is overpriced relative to guaranteed payout.`,
      });
    }
  });
  markets.forEach((m) => {
    if (!m.nestedUnder) return;
    const parent = markets.find((p) => p.id === m.nestedUnder);
    if (parent && m.yes > parent.yes) {
      flags.push({
        type: "Nested mispricing",
        market: m,
        parent,
        detail: `"${m.question}" prices at ${pct(m.yes)}, higher than its broader parent "${parent.question}" at ${pct(parent.yes)}. A specific case can't be more likely than the general one it sits inside.`,
      });
    }
  });
  return flags;
}

const EVENT_SENTIMENT = { NVDA: -0.28, TSLA: -0.22, AAPL: -0.12, XOM: 0.3, JPM: 0.08 };
const EVENT_REASON = {
  NVDA: "chip export tariff risk (34% and rising)",
  TSLA: "EV credit rollback odds rising (58%)",
  AAPL: "App Store antitrust risk, currently low (29%)",
  XOM: "OPEC+ extension favored (67%)",
  JPM: "Fed cut — steepening helps, margin hurts",
};

// ---- Shared small components ----------------------------------------------

function SentimentBadge({ score }) {
  const label = sentimentLabel(score);
  const color = sentimentColor(score);
  const Icon = label === "Bullish" ? TrendingUp : label === "Bearish" ? TrendingDown : Minus;
  return (
    <span className="inline-flex items-center gap-1 rounded-full px-2 py-[3px] text-[11px] font-semibold tracking-wide"
      style={{ color, background: `${color}1A`, border: `1px solid ${color}40` }}>
      <Icon size={12} strokeWidth={2.5} /> {label}
    </span>
  );
}

function SentimentPulse({ history }) {
  const bars = history.slice(-10);
  return (
    <div className="flex items-end gap-[3px] h-8">
      {bars.map((d, i) => {
        const h = 6 + Math.abs(d.sentiment) * 26;
        const c = sentimentColor(d.sentiment);
        return (
          <div key={i} className="w-[3px] rounded-full"
            style={{ height: `${h}px`, background: c, opacity: 0.35 + (i / bars.length) * 0.65, animation: i === bars.length - 1 ? "pulseGlow 1.8s ease-in-out infinite" : "none" }} />
        );
      })}
    </div>
  );
}

function Sparkline({ data, color }) {
  if (data.length < 2) return <svg width="90" height="28" />;
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * 88 + 1;
    const y = 26 - ((v - min) / range) * 24;
    return `${x},${y}`;
  }).join(" ");
  return <svg width="90" height="28"><polyline points={pts} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" /></svg>;
}

function Gauge({ score }) {
  const clamped = Math.max(-1, Math.min(1, score));
  const angle = (clamped + 1) * 90;
  const rad = (Math.PI * angle) / 180;
  const cx = 60, cy = 58, r = 46;
  const needleX = cx - r * Math.cos(rad), needleY = cy - r * Math.sin(rad);
  const color = sentimentColor(clamped);
  return (
    <svg width="120" height="66" viewBox="0 0 120 66">
      <path d="M 14 58 A 46 46 0 0 1 106 58" fill="none" stroke="#1A1F2B" strokeWidth="8" strokeLinecap="round" />
      <path d="M 14 58 A 46 46 0 0 1 106 58" fill="none" stroke="url(#gaugeGrad)" strokeWidth="8" strokeLinecap="round" opacity="0.9" />
      <defs><linearGradient id="gaugeGrad" x1="0" y1="0" x2="1" y2="0"><stop offset="0%" stopColor="#F87171" /><stop offset="50%" stopColor="#94A3B8" /><stop offset="100%" stopColor="#4ADE80" /></linearGradient></defs>
      <line x1={cx} y1={cy} x2={needleX} y2={needleY} stroke={color} strokeWidth="2.5" strokeLinecap="round" />
      <circle cx={cx} cy={cy} r="3.5" fill={color} />
    </svg>
  );
}

function FactorBar({ label, value, icon: Icon }) {
  const color = sentimentColor(value);
  const widthPct = (Math.abs(value) / 1) * 50;
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <Icon size={11} className="text-[#5B6478] shrink-0" />
      <span className="text-[#8B93A7] w-[70px] shrink-0">{label}</span>
      <div className="flex-1 h-1.5 rounded-full bg-[#1A1F2B] relative overflow-hidden">
        <div className="absolute top-0 bottom-0 left-1/2 w-px bg-[#2A3142]" />
        <div className="absolute top-0 bottom-0 rounded-full" style={{ width: `${widthPct}%`, background: color, left: value >= 0 ? "50%" : `${50 - widthPct}%` }} />
      </div>
      <span className="mono w-9 text-right" style={{ color }}>{value > 0 ? "+" : ""}{value.toFixed(2)}</span>
    </div>
  );
}

function OddsBar({ yes }) {
  return (
    <div className="flex h-1.5 rounded-full overflow-hidden bg-[#1A1F2B]">
      <div style={{ width: `${yes * 100}%`, background: "#4ADE80" }} />
      <div style={{ width: `${(1 - yes) * 100}%`, background: "#F87171" }} />
    </div>
  );
}

const CONNECT_NOTES = {
  sentiment: <>News is live once you hit Refresh on a ticker (calls your backend's <span className="mono text-[#5EEAD4]">/api/news/{"{ticker}"}</span>). It's on-demand rather than automatic because NewsAPI's free tier is ~100 requests/day. The 14-day trend line stays simulated — there's no historical news archive wired up yet — but the newest point turns real the moment you refresh.</>,
  markets: <>Still curated demo data. Your backend's <span className="mono text-[#5EEAD4]">/api/markets</span> can pull real Polymarket odds, but matching a real market to "which ticker does this affect" and a clean category is a curation step, not something a fetch can do on its own — worth doing by hand for the handful of markets you actually care about.</>,
  live: <>Live once your backend is running — polls <span className="mono text-[#5EEAD4]">{API_BASE}/api/prices</span> every 8 seconds. If you see "Backend offline" below, start it with <span className="mono text-[#5EEAD4]">uvicorn app.main:app --reload</span> in the backend folder.</>,
};

// ---- Main app ---------------------------------------------------------

export default function SwingIntelApp() {
  const [tab, setTab] = useState("sentiment");
  const [showConnect, setShowConnect] = useState(false);

  const [watchlist, setWatchlist] = useState(["NVDA", "TSLA", "AAPL", "XOM", "JPM"]);
  const [addValue, setAddValue] = useState("");
  const [activeTicker, setActiveTicker] = useState("NVDA");
  const [tagFilter, setTagFilter] = useState("All");
  const [history, setHistory] = useState(INITIAL_HISTORY);

  const [liveArticles, setLiveArticles] = useState({});
  const [newsLoading, setNewsLoading] = useState({});
  const [newsError, setNewsError] = useState({});

  const [categoryFilter, setCategoryFilter] = useState("All");
  const [selectedFlag, setSelectedFlag] = useState(null);
  const arbFlags = useMemo(() => detectArbitrage(MARKETS), []);

  const [prices, setPrices] = useState(() =>
    Object.fromEntries(TICKERS.map((t) => [t, { current: BASE_PRICE[t], history: [BASE_PRICE[t]] }]))
  );
  const [pricesOnline, setPricesOnline] = useState(null);
  const [weights, setWeights] = useState({ news: 0.4, event: 0.35, momentum: 0.25 });

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch(`${API_BASE}/api/prices?tickers=${TICKERS.join(",")}`, { signal: AbortSignal.timeout(6000) });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        setPrices((prev) => {
          const next = { ...prev };
          for (const t of TICKERS) {
            const q = data[t];
            if (q && typeof q.price === "number") {
              next[t] = { current: q.price, history: [...prev[t].history, q.price].slice(-30) };
            }
          }
          return next;
        });
        setPricesOnline(true);
      } catch {
        if (!cancelled) setPricesOnline(false);
      }
    }

    poll();
    const id = setInterval(poll, 8000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const refreshNews = useCallback(async (ticker) => {
    setNewsLoading((s) => ({ ...s, [ticker]: true }));
    setNewsError((s) => ({ ...s, [ticker]: null }));
    try {
      const res = await fetch(`${API_BASE}/api/news/${ticker}`, { signal: AbortSignal.timeout(8000) });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const articles = (data.articles || []).map((a, i) => ({
        id: `live-${ticker}-${i}`,
        ticker,
        tag: "Live",
        source: a.source || "Unknown",
        time: a.time ? new Date(a.time).toLocaleString() : "",
        headline: a.headline,
        score: a.score,
        magnitude: a.magnitude,
        url: a.url,
      }));
      setLiveArticles((s) => ({ ...s, [ticker]: articles }));

      if (articles.length) {
        const avg = articles.reduce((sum, a) => sum + a.score * a.magnitude, 0) / articles.reduce((sum, a) => sum + a.magnitude, 0);
        setHistory((h) => {
          const hist = h[ticker] || [];
          const nextPoint = { day: "Live", sentiment: Number(avg.toFixed(2)) };
          return { ...h, [ticker]: [...hist.slice(0, -1), nextPoint] };
        });
      }
    } catch (e) {
      setNewsError((s) => ({ ...s, [ticker]: e.message || "Request failed" }));
    } finally {
      setNewsLoading((s) => ({ ...s, [ticker]: false }));
    }
  }, []);

  const filteredArticles = useMemo(() => {
    const source = liveArticles[activeTicker] || MOCK_ARTICLES.filter((a) => a.ticker === activeTicker);
    return source.filter((a) => tagFilter === "All" || a.tag === tagFilter).sort((a, b) => b.magnitude - a.magnitude);
  }, [activeTicker, tagFilter, liveArticles]);

  const isLiveNews = Boolean(liveArticles[activeTicker]);
  const activeHistory = history[activeTicker] || [];
  const currentScore = activeHistory[activeHistory.length - 1]?.sentiment ?? 0;
  const priorScore = activeHistory[activeHistory.length - 4]?.sentiment ?? 0;
  const momentumSent = currentScore - priorScore;

  function addTicker() {
    const v = addValue.trim().toUpperCase();
    if (v && !watchlist.includes(v)) {
      setWatchlist([...watchlist, v]);
      if (!history[v]) setHistory((h) => ({ ...h, [v]: buildHistory(Math.random() - 0.5) }));
    }
    setAddValue("");
  }
  function removeTicker(t) {
    setWatchlist(watchlist.filter((x) => x !== t));
    if (activeTicker === t && watchlist.length > 1) setActiveTicker(watchlist.find((x) => x !== t));
  }

  const weightSum = weights.news + weights.event + weights.momentum || 1;
  const normWeights = { news: weights.news / weightSum, event: weights.event / weightSum, momentum: weights.momentum / weightSum };

  const predictions = useMemo(() => {
    return TICKERS.map((t) => {
      const hist = prices[t].history;
      const priceMomentum = hist.length >= 2 ? Math.max(-1, Math.min(1, ((hist[hist.length - 1] - hist[0]) / hist[0]) * 40)) : 0;
      const news = history[t]?.[history[t].length - 1]?.sentiment ?? 0;
      const event = EVENT_SENTIMENT[t];
      const composite = news * normWeights.news + event * normWeights.event + priceMomentum * normWeights.momentum;
      return { ticker: t, news, event, momentum: priceMomentum, composite };
    });
  }, [prices, history, normWeights.news, normWeights.event, normWeights.momentum]);

  const TABS = [
    { id: "sentiment", label: "Sentiment" },
    { id: "markets", label: "Prediction Markets" },
    { id: "live", label: "Live & Prediction" },
  ];

  return (
    <div className="min-h-screen w-full bg-[#0B0E14] text-[#E8EAED] font-sans">
      <style>{`
        @keyframes pulseGlow { 0%,100%{opacity:.6;filter:brightness(1)} 50%{opacity:1;filter:brightness(1.5)} }
        @keyframes livedot { 0%,100%{opacity:1} 50%{opacity:.25} }
        @keyframes spin { to { transform: rotate(360deg); } }
        .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }
        .display { font-family: 'Space Grotesk', system-ui, sans-serif; }
        input[type=range] { accent-color: #5EEAD4; }
      `}</style>

      <div className="border-b border-[#1E2330] bg-[#0D111A] sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-5 py-4 flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 rounded-full bg-[#5EEAD4]" style={{ animation: "livedot 1.6s ease-in-out infinite" }} />
            <h1 className="display text-lg font-bold tracking-tight">SwingIntel</h1>
            {pricesOnline !== null && (
              <span className="flex items-center gap-1 text-[11px] font-medium px-2 py-[3px] rounded-full"
                style={{ color: pricesOnline ? "#4ADE80" : "#FBBF24", background: pricesOnline ? "#4ADE801A" : "#FBBF241A" }}>
                {pricesOnline ? <Wifi size={11} /> : <WifiOff size={11} />}
                {pricesOnline ? "Backend live" : "Backend offline"}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1 bg-[#0F131C] border border-[#1E2330] rounded-lg p-1">
            {TABS.map((t) => (
              <button key={t.id} onClick={() => setTab(t.id)}
                className="text-[12px] font-medium px-3 py-1.5 rounded-md transition-colors"
                style={{ color: tab === t.id ? "#0B0E14" : "#8B93A7", background: tab === t.id ? "#5EEAD4" : "transparent" }}>
                {t.label}
              </button>
            ))}
          </div>
          <button onClick={() => setShowConnect(!showConnect)}
            className="flex items-center gap-1.5 text-[12px] text-[#8B93A7] hover:text-[#E8EAED] border border-[#232838] rounded-md px-2.5 py-1.5 transition-colors">
            <Info size={13} /> Connect live data
          </button>
        </div>
        {showConnect && (
          <div className="max-w-6xl mx-auto px-5 pb-4 text-[12px] text-[#8B93A7] leading-relaxed">
            {CONNECT_NOTES[tab]}
          </div>
        )}
        {pricesOnline === false && (
          <div className="max-w-6xl mx-auto px-5 pb-3 text-[11px] text-[#FBBF24] flex items-center gap-1.5">
            <WifiOff size={12} /> Can't reach the backend at {API_BASE} — start it with <span className="mono">uvicorn app.main:app --reload</span> in the backend folder. Showing last known prices.
          </div>
        )}
      </div>

      {tab === "sentiment" && (
        <div className="max-w-6xl mx-auto px-5 py-6 grid grid-cols-12 gap-5">
          <div className="col-span-12 lg:col-span-3">
            <div className="text-[11px] uppercase tracking-wider text-[#5B6478] mb-2 font-semibold">Watchlist</div>
            <div className="flex flex-col gap-1.5">
              {watchlist.map((t) => {
                const h = history[t] || [];
                const score = h[h.length - 1]?.sentiment ?? 0;
                const active = t === activeTicker;
                return (
                  <button key={t} onClick={() => setActiveTicker(t)}
                    className="group flex items-center justify-between rounded-lg px-3 py-2.5 text-left transition-colors"
                    style={{ background: active ? "#151A26" : "transparent", border: `1px solid ${active ? "#2A3142" : "transparent"}` }}>
                    <div className="flex items-center gap-3">
                      <span className="mono text-[13px] font-bold w-12">{t}</span>
                      <SentimentPulse history={h} />
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="mono text-[11px]" style={{ color: sentimentColor(score) }}>{score > 0 ? "+" : ""}{score.toFixed(2)}</span>
                      <X size={13} className="text-[#3A4155] opacity-0 group-hover:opacity-100 hover:text-[#F87171] transition-opacity" onClick={(e) => { e.stopPropagation(); removeTicker(t); }} />
                    </div>
                  </button>
                );
              })}
            </div>
            <div className="flex items-center gap-1.5 mt-3">
              <input value={addValue} onChange={(e) => setAddValue(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addTicker()}
                placeholder="Add ticker" className="mono flex-1 bg-[#0F131C] border border-[#232838] rounded-md px-2.5 py-1.5 text-[12px] outline-none focus:border-[#5EEAD4] placeholder:text-[#4A5266]" />
              <button onClick={addTicker} className="bg-[#151A26] border border-[#232838] rounded-md p-1.5 hover:border-[#5EEAD4] transition-colors"><Plus size={14} /></button>
            </div>

            <div className="mt-7">
              <div className="text-[11px] uppercase tracking-wider text-[#5B6478] mb-2 font-semibold">Sector outlook</div>
              <div className="bg-[#0F131C] border border-[#1E2330] rounded-lg p-3">
                <ResponsiveContainer width="100%" height={170}>
                  <BarChart data={SECTORS} layout="vertical" margin={{ left: 0, right: 12, top: 0, bottom: 0 }}>
                    <XAxis type="number" domain={[-1, 1]} hide />
                    <YAxis type="category" dataKey="name" width={110} tick={{ fill: "#8B93A7", fontSize: 10 }} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={{ background: "#151A26", border: "1px solid #232838", borderRadius: 8, fontSize: 12 }} formatter={(v) => [v.toFixed(2), "Sentiment"]} />
                    <Bar dataKey="score" radius={[0, 4, 4, 0]} barSize={12}>
                      {SECTORS.map((s, i) => <Cell key={i} fill={sentimentColor(s.score)} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
                <p className="text-[11px] text-[#5B6478] mt-1 leading-relaxed">Ranked by aggregate news sentiment across each sector's tracked tickers.</p>
              </div>
            </div>
          </div>

          <div className="col-span-12 lg:col-span-9 flex flex-col gap-5">
            <div className="bg-[#0F131C] border border-[#1E2330] rounded-xl p-5">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <div className="flex items-center gap-2.5">
                    <h2 className="display text-2xl font-bold mono">{activeTicker}</h2>
                    <SentimentBadge score={currentScore} />
                  </div>
                  <p className="text-[12px] text-[#8B93A7] mt-1">
                    {momentumSent > 0.05 ? "Sentiment building over the last few sessions" : momentumSent < -0.05 ? "Sentiment cooling over the last few sessions" : "Sentiment holding steady"}
                    <span className="mono ml-1" style={{ color: sentimentColor(momentumSent) }}>({momentumSent > 0 ? "+" : ""}{momentumSent.toFixed(2)} 3d)</span>
                  </p>
                </div>
                <div className="text-right">
                  <div className="text-[11px] text-[#5B6478] uppercase tracking-wider">Current score</div>
                  <div className="mono text-2xl font-bold" style={{ color: sentimentColor(currentScore) }}>{currentScore > 0 ? "+" : ""}{currentScore.toFixed(2)}</div>
                </div>
              </div>
              <ResponsiveContainer width="100%" height={140}>
                <AreaChart data={activeHistory} margin={{ left: -20, right: 10, top: 5, bottom: 0 }}>
                  <defs><linearGradient id="sentGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#5EEAD4" stopOpacity={0.35} /><stop offset="100%" stopColor="#5EEAD4" stopOpacity={0} /></linearGradient></defs>
                  <CartesianGrid stroke="#1A1F2B" vertical={false} />
                  <XAxis dataKey="day" tick={{ fill: "#5B6478", fontSize: 10 }} axisLine={false} tickLine={false} />
                  <YAxis domain={[-1, 1]} tick={{ fill: "#5B6478", fontSize: 10 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{ background: "#151A26", border: "1px solid #232838", borderRadius: 8, fontSize: 12 }} labelStyle={{ color: "#8B93A7" }} />
                  <Area type="monotone" dataKey="sentiment" stroke="#5EEAD4" strokeWidth={2} fill="url(#sentGrad)" />
                </AreaChart>
              </ResponsiveContainer>
              <p className="text-[11px] text-[#5B6478] mt-1">14-session trend is simulated (no historical news archive yet) — the most recent point is real once you refresh news below.</p>
            </div>

            <div>
              <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                <div className="flex items-center gap-2">
                  <Newspaper size={15} className="text-[#5EEAD4]" />
                  <h3 className="text-[13px] font-semibold uppercase tracking-wider text-[#8B93A7]">News feed — {activeTicker}</h3>
                  {isLiveNews && <span className="flex items-center gap-1 text-[10px] font-semibold px-1.5 py-[2px] rounded text-[#5EEAD4] bg-[#5EEAD41A]"><Wifi size={9} /> Live</span>}
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex gap-1.5">
                    {["All", "Earnings", "Political", "Macro", "Sector", "Live"].map((tg) => (
                      <button key={tg} onClick={() => setTagFilter(tg)} className="text-[11px] px-2.5 py-1 rounded-full border transition-colors"
                        style={{ color: tagFilter === tg ? "#0B0E14" : "#8B93A7", background: tagFilter === tg ? "#5EEAD4" : "transparent", borderColor: tagFilter === tg ? "#5EEAD4" : "#232838" }}>{tg}</button>
                    ))}
                  </div>
                  <button onClick={() => refreshNews(activeTicker)} disabled={newsLoading[activeTicker]}
                    className="flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-full border border-[#232838] text-[#8B93A7] hover:border-[#5EEAD4] hover:text-[#E8EAED] transition-colors disabled:opacity-50">
                    <RefreshCw size={11} style={newsLoading[activeTicker] ? { animation: "spin 0.8s linear infinite" } : {}} />
                    {newsLoading[activeTicker] ? "Fetching…" : "Refresh"}
                  </button>
                </div>
              </div>
              {newsError[activeTicker] && (
                <div className="text-[11px] text-[#FBBF24] mb-2 flex items-center gap-1.5"><AlertTriangle size={11} /> {newsError[activeTicker]}</div>
              )}
              <div className="flex flex-col gap-2">
                {filteredArticles.length === 0 && (
                  <div className="text-center py-10 text-[#5B6478] text-[13px] border border-dashed border-[#232838] rounded-lg">No {tagFilter !== "All" ? tagFilter.toLowerCase() : ""} headlines for {activeTicker} right now.</div>
                )}
                {filteredArticles.map((a) => {
                  const meta = TAG_META[a.tag] || TAG_META.Sector;
                  const TagIcon = meta.icon;
                  return (
                    <div key={a.id} className="bg-[#0F131C] border border-[#1E2330] rounded-lg p-3.5 hover:border-[#2A3142] transition-colors">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-1.5">
                            <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-[2px] rounded" style={{ color: meta.color, background: `${meta.color}1A` }}><TagIcon size={10} /> {a.tag}</span>
                            <span className="text-[11px] text-[#5B6478]">{a.source} · {a.time}</span>
                          </div>
                          {a.url ? (
                            <a href={a.url} target="_blank" rel="noreferrer" className="text-[13.5px] leading-snug text-[#D5D9E0] hover:text-[#5EEAD4] hover:underline">{a.headline}</a>
                          ) : (
                            <p className="text-[13.5px] leading-snug text-[#D5D9E0]">{a.headline}</p>
                          )}
                        </div>
                        <div className="flex flex-col items-end gap-1.5 shrink-0">
                          <SentimentBadge score={a.score} />
                          <div className="w-16 h-1 rounded-full bg-[#1A1F2B] overflow-hidden"><div className="h-full rounded-full" style={{ width: `${a.magnitude * 100}%`, background: sentimentColor(a.score) }} /></div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {tab === "markets" && (
        <div className="max-w-6xl mx-auto px-5 py-6 grid grid-cols-12 gap-5">
          <div className="col-span-12 lg:col-span-8 flex flex-col gap-4">
            <div className="flex gap-1.5 flex-wrap">
              {["All", ...Object.keys(CATEGORY_META)].map((cat) => (
                <button key={cat} onClick={() => setCategoryFilter(cat)} className="text-[11px] px-2.5 py-1 rounded-full border transition-colors"
                  style={{ color: categoryFilter === cat ? "#0B0E14" : "#8B93A7", background: categoryFilter === cat ? "#5EEAD4" : "transparent", borderColor: categoryFilter === cat ? "#5EEAD4" : "#232838" }}>{cat}</button>
              ))}
            </div>
            <div className="flex flex-col gap-2.5">
              {MARKETS.filter((m) => categoryFilter === "All" || m.category === categoryFilter).map((m) => {
                const meta = CATEGORY_META[m.category];
                const Icon = meta.icon;
                const flagged = arbFlags.some((f) => f.market.id === m.id);
                return (
                  <div key={m.id} className="bg-[#0F131C] border rounded-lg p-4 transition-colors" style={{ borderColor: flagged ? "#FBBF2455" : "#1E2330" }}>
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-[2px] rounded" style={{ color: meta.color, background: `${meta.color}1A` }}><Icon size={10} /> {m.category}</span>
                          {flagged && <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-[2px] rounded text-[#FBBF24] bg-[#FBBF241A]"><AlertTriangle size={10} /> Mispricing</span>}
                          <span className="text-[11px] text-[#5B6478]">{m.volume} vol</span>
                        </div>
                        <p className="text-[13.5px] text-[#D5D9E0] leading-snug mb-2.5">{m.question}</p>
                        <OddsBar yes={m.yes} />
                        <div className="flex items-center justify-between mt-1.5 text-[11px]">
                          <span className="mono text-[#4ADE80] font-semibold">YES {pct(m.yes)}</span>
                          <span className="mono text-[#F87171] font-semibold">NO {pct(m.no)}</span>
                        </div>
                        <div className="mt-2.5 flex flex-col gap-1">
                          {m.impacts.map((imp, i) => (
                            <div key={i} className="flex items-center gap-1.5 text-[11px] text-[#8B93A7]"><ChevronRight size={10} className="text-[#3A4155]" /><span className="mono text-[#5EEAD4]">{imp.ticker}</span> — {imp.note}</div>
                          ))}
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-1 shrink-0">
                        <div className="text-[11px] text-[#5B6478]">24h</div>
                        <div className="flex items-center gap-1 mono text-[12px] font-semibold" style={{ color: m.change24h >= 0 ? "#4ADE80" : "#F87171" }}>
                          {m.change24h >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}{m.change24h >= 0 ? "+" : ""}{Math.round(m.change24h * 100)}pt
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="col-span-12 lg:col-span-4">
            <div className="flex items-center gap-2 mb-2"><ArrowRightLeft size={15} className="text-[#FBBF24]" /><h3 className="text-[13px] font-semibold uppercase tracking-wider text-[#8B93A7]">Arbitrage watch</h3></div>
            {arbFlags.length === 0 ? (
              <div className="text-center py-8 text-[#5B6478] text-[12px] border border-dashed border-[#232838] rounded-lg px-3">No mispricings detected across tracked markets right now.</div>
            ) : (
              <div className="flex flex-col gap-2">
                {arbFlags.map((f, i) => (
                  <button key={i} onClick={() => setSelectedFlag(selectedFlag === i ? null : i)} className="text-left bg-[#0F131C] border border-[#232838] rounded-lg p-3 hover:border-[#FBBF2455] transition-colors">
                    <div className="flex items-center gap-1.5 mb-1"><AlertTriangle size={11} className="text-[#FBBF24]" /><span className="text-[11px] font-semibold text-[#FBBF24]">{f.type}</span></div>
                    <p className="text-[12px] text-[#D5D9E0] leading-snug mb-1">{f.market.question}</p>
                    {selectedFlag === i && <p className="text-[11px] text-[#8B93A7] leading-relaxed mt-1.5 pt-1.5 border-t border-[#1E2330]">{f.detail}</p>}
                  </button>
                ))}
              </div>
            )}
            <div className="mt-5 bg-[#0F131C] border border-[#1E2330] rounded-lg p-3.5">
              <p className="text-[11px] text-[#5B6478] leading-relaxed"><span className="text-[#8B93A7] font-semibold">How this works:</span> "Risk-free" flags a same-market pricing gap where YES + NO don't sum to $1. "Nested mispricing" flags a specific outcome priced above the broader outcome it sits inside — logically impossible, and a signal worth watching even though it isn't free money.</p>
            </div>
          </div>
        </div>
      )}

      {tab === "live" && (
        <div className="max-w-6xl mx-auto px-5 py-6 flex flex-col gap-7">
          <div>
            <div className="flex items-center gap-2 mb-3"><Activity size={15} className="text-[#5EEAD4]" /><h3 className="text-[13px] font-semibold uppercase tracking-wider text-[#8B93A7]">Live prices</h3></div>
            <div className="bg-[#0F131C] border border-[#1E2330] rounded-lg overflow-hidden">
              {TICKERS.map((t, i) => {
                const p = prices[t];
                const open = p.history[0];
                const changePct = ((p.current - open) / open) * 100;
                const color = changePct >= 0 ? "#4ADE80" : "#F87171";
                return (
                  <div key={t} className="flex items-center justify-between px-4 py-3" style={{ borderTop: i === 0 ? "none" : "1px solid #1A1F2B" }}>
                    <span className="mono text-[13px] font-bold w-14">{t}</span>
                    <Sparkline data={p.history} color={color} />
                    <span className="mono text-[13px] font-semibold w-20 text-right">${p.current.toFixed(2)}</span>
                    <span className="mono text-[12px] font-semibold w-20 text-right flex items-center justify-end gap-1" style={{ color }}>
                      {changePct >= 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />}{changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%
                    </span>
                  </div>
                );
              })}
            </div>
            <p className="text-[11px] text-[#5B6478] mt-1.5">
              {pricesOnline ? "Polling your backend every ~8 seconds · session open used as the day's reference price." : "Waiting for backend connection — see the banner above."}
            </p>
          </div>

          <div>
            <div className="flex items-center justify-between mb-3 flex-wrap gap-3">
              <div className="flex items-center gap-2"><GaugeIcon size={15} className="text-[#5EEAD4]" /><h3 className="text-[13px] font-semibold uppercase tracking-wider text-[#8B93A7]">Composite prediction</h3></div>
              <div className="flex items-center gap-4 text-[11px] text-[#8B93A7]">
                {["news", "event", "momentum"].map((k) => (
                  <div key={k} className="flex items-center gap-1.5">
                    <span className="capitalize w-14">{k}</span>
                    <input type="range" min="0" max="1" step="0.05" value={weights[k]} onChange={(e) => setWeights({ ...weights, [k]: Number(e.target.value) })} className="w-20" />
                    <span className="mono w-8">{Math.round(normWeights[k] * 100)}%</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {predictions.map((p) => {
                const label = sentimentLabel(p.composite);
                const color = sentimentColor(p.composite);
                const topFactor = Math.abs(p.news) >= Math.abs(p.event) && Math.abs(p.news) >= Math.abs(p.momentum)
                  ? "recent news sentiment" : Math.abs(p.event) >= Math.abs(p.momentum) ? EVENT_REASON[p.ticker] : "recent price action";
                return (
                  <button key={p.ticker} onClick={() => { setActiveTicker(p.ticker); setTab("sentiment"); }} className="text-left bg-[#0F131C] border border-[#1E2330] rounded-xl p-4 hover:border-[#2A3142] transition-colors">
                    <div className="flex items-center justify-between">
                      <div><div className="mono text-[15px] font-bold">{p.ticker}</div><div className="text-[12px] font-semibold" style={{ color }}>{label}</div></div>
                      <Gauge score={p.composite} />
                    </div>
                    <div className="mt-3 flex flex-col gap-1.5">
                      <FactorBar label="News" value={p.news} icon={Newspaper} />
                      <FactorBar label="Event odds" value={p.event} icon={Landmark} />
                      <FactorBar label="Momentum" value={p.momentum} icon={Activity} />
                    </div>
                    <p className="text-[11px] text-[#8B93A7] leading-relaxed mt-3 pt-3 border-t border-[#1A1F2B]">{label} lean, driven mainly by {topFactor}.</p>
                  </button>
                );
              })}
            </div>
            <p className="text-[11px] text-[#5B6478] mt-3">Composite score = weighted blend of the three factors above. Momentum and price are live once your backend is running; news becomes live per ticker after you hit Refresh; event odds are curated for now. Click a card to jump to that ticker's news.</p>
          </div>
        </div>
      )}
    </div>
  );
}
