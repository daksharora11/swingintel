import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { RefreshCw, AlertTriangle, Plus, X, Bell, Clock, Target, Layers, Zap, Info } from "lucide-react";
import OptionsJournal from "./OptionsJournal.jsx";
import { DIR_COLOR, DIR_LABEL, usd, num, fmtAge, ageFrom, shortDate, shortContract, api } from "./optionsUtil.js";

// ---------------------------------------------------------------------------
// Options Desk: paper-trading idea board.
// Every tile shows how old its data is. Entries are blocked when the market is
// closed or quotes are stale. Nothing here places a real trade.
// ---------------------------------------------------------------------------

const CARD = "bg-[#0F131C] border border-[#1E2330] rounded-lg";
const LABEL = "text-[11px] uppercase tracking-wider text-[#8B93A7]";

function Stat({ label, value, sub, tone }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-[#5B6478]">{label}</span>
      <span className="mono text-[14px] font-semibold" style={{ color: tone || "#E8EAED" }}>{value}</span>
      {sub && <span className="text-[10px] text-[#5B6478]">{sub}</span>}
    </div>
  );
}

function FreshChip({ label, ageS, fresh, note }) {
  const color = fresh ? "#4ADE80" : "#FBBF24";
  return (
    <span title={note || ""} className="flex items-center gap-1 text-[10px] px-2 py-[3px] rounded-full mono"
      style={{ color, background: color + "1A" }}>
      <Clock size={10} /> {label} {ageS == null ? "?" : fmtAge(ageS)}
    </span>
  );
}

function ConvictionBar({ value, color }) {
  return (
    <div className="flex items-center gap-2 min-w-[90px]">
      <div className="h-1.5 flex-1 rounded-full bg-[#1E2330] overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${Math.min(100, value)}%`, background: color }} />
      </div>
      <span className="mono text-[11px] w-6 text-right" style={{ color }}>{value}</span>
    </div>
  );
}

function EntryBadge({ d }) {
  if (d.direction === "no_trade") return <span className="text-[10px] text-[#5B6478]">skip</span>;
  if (!d.meta.market_open) return <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#FBBF241A] text-[#FBBF24]">CLOSED</span>;
  if (!d.actionable) return <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#FBBF241A] text-[#FBBF24]">BLOCKED</span>;
  if (d.plan?.status === "enter_now") return <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#4ADE801A] text-[#4ADE80] font-semibold">ENTER</span>;
  return <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#5EEAD41A] text-[#5EEAD4]">WAIT</span>;
}

// ---------------------------------------------------------------------------
// Price ladder: every level that matters, sorted by price, with distance from spot
// ---------------------------------------------------------------------------
function Ladder({ d }) {
  const spot = d.spot;
  const L = d.levels, P = d.plan;
  const items = [];
  const add = (label, price, tone) => { if (price != null && !Number.isNaN(price)) items.push({ label, price, tone }); };
  (L.resistances || []).forEach((p, i) => add(`Resistance ${i + 1}`, p, "#F87171"));
  (L.supports || []).forEach((p, i) => add(`Support ${i + 1}`, p, "#4ADE80"));
  add("Call wall (max call OI)", L.call_wall, "#F59E0B");
  add("Put wall (max put OI)", L.put_wall, "#F59E0B");
  add("Max pain", L.max_pain, "#8B93A7");
  add("VWAP", L.vwap, "#A78BFA");
  add("20d avg", L.sma20, "#5B6478");
  add("50d avg", L.sma50, "#5B6478");
  if (P) {
    add("Target 2", P.target2, "#5EEAD4");
    add("Target 1", P.target1, "#5EEAD4");
    add("Stop", P.stop, "#F87171");
    if (P.limit_entry != null) add("Limit entry", P.limit_entry, "#5EEAD4");
  }
  add("Expected move up", d.volatility.expected_move ? spot + d.volatility.expected_move : null, "#3B4256");
  add("Expected move down", d.volatility.expected_move ? spot - d.volatility.expected_move : null, "#3B4256");
  items.push({ label: "SPOT", price: spot, tone: "#E8EAED", spot: true });
  items.sort((a, b) => b.price - a.price);
  return (
    <div className="flex flex-col">
      {items.map((it, i) => (
        <div key={i} className="flex items-center justify-between py-[5px] px-2 text-[12px] border-b border-[#161B27] last:border-0"
          style={it.spot ? { background: "#1E2330", borderRadius: 6 } : {}}>
          <span className="flex items-center gap-2" style={{ color: it.tone }}>
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: it.tone }} /> {it.label}
          </span>
          <span className="mono text-[#D5D9E0]">
            {num(it.price)} {!it.spot && <span className="text-[#5B6478] ml-1">{((it.price / spot - 1) * 100).toFixed(1)}%</span>}
          </span>
        </div>
      ))}
    </div>
  );
}

function FactorRow({ name, f, contrib }) {
  const labels = { trend: "Trend & momentum", flow: "Options flow", news: "News sentiment", political: "Policy / Trump", insider: "Insider filings", regime: "Market regime" };
  const s = f.score ?? 0;
  const color = !f.available ? "#3B4256" : s > 0.1 ? "#4ADE80" : s < -0.1 ? "#F87171" : "#8B93A7";
  return (
    <div className="py-2 border-b border-[#161B27] last:border-0">
      <div className="flex items-center justify-between text-[12px]">
        <span className="text-[#D5D9E0]">{labels[name] || name}</span>
        <span className="mono" style={{ color }}>{f.available ? (s >= 0 ? "+" : "") + s.toFixed(2) : "no data"}</span>
      </div>
      <div className="relative h-1.5 rounded-full bg-[#1E2330] my-1.5">
        <div className="absolute top-0 bottom-0 w-px bg-[#3B4256] left-1/2" />
        {f.available && (
          <div className="absolute top-0 bottom-0 rounded-full"
            style={{ background: color, left: s >= 0 ? "50%" : `${50 + s * 50}%`, width: `${Math.abs(s) * 50}%` }} />
        )}
      </div>
      <div className="text-[10px] text-[#5B6478] flex justify-between gap-3">
        <span>{f.detail}</span>
        {contrib != null && <span className="mono shrink-0">weighted {contrib >= 0 ? "+" : ""}{contrib.toFixed(2)}</span>}
      </div>
      {name === "political" && f.evidence?.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-1">
          {f.evidence.slice(0, 3).map((e, i) => (
            <li key={i} className="text-[11px] text-[#8B93A7] leading-snug">
              <span className="mono" style={{ color: e.effect > 0 ? "#4ADE80" : "#F87171" }}>{e.effect > 0 ? "+" : ""}{e.effect}</span> {e.headline}
              <span className="text-[#5B6478]"> ({e.matched})</span>
            </li>
          ))}
        </ul>
      )}
      {name === "news" && f.headlines?.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-1">
          {f.headlines.slice(0, 3).map((h, i) => (
            <li key={i} className="text-[11px] text-[#8B93A7] leading-snug">
              <span className="mono" style={{ color: h.score > 0 ? "#4ADE80" : h.score < 0 ? "#F87171" : "#5B6478" }}>{h.score > 0 ? "+" : ""}{h.score}</span> {h.headline}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chain heatmap
// ---------------------------------------------------------------------------
function ChainHeatmap({ apiBase, ticker, expirations, defaultExpiry }) {
  const [expiry, setExpiry] = useState(defaultExpiry);
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => { setExpiry(defaultExpiry); }, [defaultExpiry, ticker]);
  const load = useCallback(async () => {
    if (!expiry) return;
    setLoading(true); setErr(null);
    try { setData(await api(apiBase, `/api/options/${ticker}/chain?expiry=${expiry}&width=7`)); }
    catch (e) { setErr(e.message); }
    finally { setLoading(false); }
  }, [apiBase, ticker, expiry]);
  useEffect(() => { load(); }, [load]);

  const maxVol = useMemo(() => {
    if (!data) return 1;
    return Math.max(1, ...data.strikes.flatMap((s) => [s.call?.volume || 0, s.put?.volume || 0]));
  }, [data]);
  const cell = (side, key, fmt) => <td className="px-2 py-1 mono text-right">{side && side[key] != null ? fmt(side[key]) : "–"}</td>;
  const heat = (v, c) => ({ background: `rgba(${c}, ${Math.min(0.55, (v || 0) / maxVol * 0.8)})` });

  return (
    <div className={`${CARD} p-4`}>
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div className="flex items-center gap-2"><Layers size={14} className="text-[#5EEAD4]" /><h4 className={LABEL}>Option chain</h4></div>
        <div className="flex items-center gap-2">
          <select value={expiry || ""} onChange={(e) => setExpiry(e.target.value)}
            className="bg-[#0B0E14] border border-[#232838] rounded-md px-2 py-1 text-[11px] mono text-[#D5D9E0]">
            {expirations.map((x) => <option key={x.date} value={x.date}>{shortDate(x.date)} ({x.dte}d)</option>)}
          </select>
          <button onClick={load} className="text-[#8B93A7] hover:text-[#E8EAED]"><RefreshCw size={12} style={loading ? { animation: "spin 0.8s linear infinite" } : {}} /></button>
        </div>
      </div>
      {err && <div className="text-[12px] text-[#FBBF24]">{err}</div>}
      {data && (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] text-[#D5D9E0]">
            <thead>
              <tr className="text-[#5B6478] text-[10px] uppercase">
                <th className="px-2 py-1 text-right">Vol</th><th className="px-2 text-right">OI</th><th className="px-2 text-right">IV</th><th className="px-2 text-right">Δ</th><th className="px-2 text-right">Mid</th>
                <th className="px-2 text-center bg-[#0B0E14]">Strike</th>
                <th className="px-2 text-right">Mid</th><th className="px-2 text-right">Δ</th><th className="px-2 text-right">IV</th><th className="px-2 text-right">OI</th><th className="px-2 text-right">Vol</th>
              </tr>
            </thead>
            <tbody>
              {data.strikes.map((s) => {
                const atm = Math.abs(s.strike - data.spot) === Math.min(...data.strikes.map((x) => Math.abs(x.strike - data.spot)));
                const mid = (x) => (x && x.bid > 0 && x.ask > 0 ? ((x.bid + x.ask) / 2).toFixed(2) : x?.last != null ? x.last.toFixed(2) : "–");
                return (
                  <tr key={s.strike} className="border-t border-[#161B27]" style={s.call?.itm ? { background: "#4ADE8008" } : s.put?.itm ? { background: "#F8717108" } : {}}>
                    <td className="px-2 py-1 mono text-right" style={heat(s.call?.volume, "74,222,128")}>{s.call?.volume ?? "–"}</td>
                    {cell(s.call, "oi", (v) => v)}
                    {cell(s.call, "iv", (v) => (v * 100).toFixed(0) + "%")}
                    {cell(s.call, "delta", (v) => v.toFixed(2))}
                    <td className="px-2 py-1 mono text-right">{mid(s.call)}</td>
                    <td className="px-2 py-1 mono text-center font-semibold bg-[#0B0E14]" style={{ color: atm ? "#5EEAD4" : "#E8EAED" }}>
                      {s.strike}
                      {s.strike === data.max_pain && <span className="ml-1 text-[9px] text-[#8B93A7]">pain</span>}
                      {s.strike === data.call_wall && <span className="ml-1 text-[9px] text-[#F59E0B]">C wall</span>}
                      {s.strike === data.put_wall && <span className="ml-1 text-[9px] text-[#F59E0B]">P wall</span>}
                    </td>
                    <td className="px-2 py-1 mono text-right">{mid(s.put)}</td>
                    {cell(s.put, "delta", (v) => v.toFixed(2))}
                    {cell(s.put, "iv", (v) => (v * 100).toFixed(0) + "%")}
                    {cell(s.put, "oi", (v) => v)}
                    <td className="px-2 py-1 mono text-right" style={heat(s.put?.volume, "248,113,113")}>{s.put?.volume ?? "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="text-[10px] text-[#5B6478] mt-2">Chain fetched {fmtAge(ageFrom(data.fetched_at))} ago. Yahoo option quotes are normally about 15 minutes behind the market.</div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------
function Detail({ d, apiBase, onRefresh, loading, onLogged }) {
  const [contracts, setContracts] = useState(1);
  const [ownFill, setOwnFill] = useState("");
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const dirColor = DIR_COLOR[d.direction];
  const c = d.contract, p = d.plan, v = d.volatility;

  const log = async () => {
    setBusy(true); setMsg(null);
    try {
      const factors = Object.fromEntries(Object.entries(d.factors).filter(([, f]) => f.available).map(([k, f]) => [k, f.score]));
      const t = await api(apiBase, "/api/options/trades", {
        method: "POST",
        body: JSON.stringify({
          ticker: d.ticker, right: d.direction, strike: c.strike, expiry: c.expiry, contracts: Number(contracts) || 1,
          conviction: d.conviction, stop_underlying: p.stop, target_underlying: p.target1, factors,
          entry_premium: ownFill ? Number(ownFill) : null, source: ownFill ? "manual" : "model",
        }),
      });
      setMsg({ ok: true, text: `Logged ${d.ticker} ${shortContract(c)} x${t.contracts} at $${Number(t.entry_premium).toFixed(2)}` });
      if (onLogged) onLogged();
    } catch (e) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(false); }
  };

  const canLog = d.direction !== "no_trade" && c?.found && p;
  const quoteAge = d.meta.quote.age_s;

  return (
    <div className="flex flex-col gap-4">
      <div className={`${CARD} p-4 flex flex-col gap-3`}>
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <h3 className="display text-xl font-bold">{d.ticker}</h3>
            <span className="mono text-lg">${num(d.spot)}</span>
            <span className="px-2.5 py-1 rounded-md text-[12px] font-bold tracking-wide" style={{ color: dirColor, background: dirColor + "1A" }}>
              {DIR_LABEL[d.direction]}
            </span>
            <EntryBadge d={d} />
          </div>
          <div className="flex items-center gap-1.5 flex-wrap">
            <FreshChip label="quote" ageS={quoteAge} fresh={d.meta.quote.fresh} note={d.meta.quote.label} />
            <FreshChip label="chain" ageS={d.meta.chain.age_s} fresh={d.meta.market_open && (d.meta.chain.age_s ?? 1e9) < 30 * 60} note={d.meta.chain.note} />
            <FreshChip label="news" ageS={ageFrom(d.meta.news.fetched_at)} fresh={(ageFrom(d.meta.news.fetched_at) ?? 1e9) < 15 * 60} note={`source: ${d.meta.news.source || "none"}`} />
            <FreshChip label="policy" ageS={ageFrom(d.meta.political.fetched_at)} fresh={(ageFrom(d.meta.political.fetched_at) ?? 1e9) < 15 * 60} note={`sources: ${(d.meta.political.sources || []).join(", ") || "none"}`} />
            <button onClick={onRefresh} className="flex items-center gap-1 text-[11px] px-2 py-[3px] rounded-full border border-[#232838] text-[#8B93A7] hover:text-[#E8EAED] hover:border-[#5EEAD4]">
              <RefreshCw size={11} style={loading ? { animation: "spin 0.8s linear infinite" } : {}} /> Refresh now
            </button>
          </div>
        </div>

        {!d.meta.market_open && (
          <div className="text-[12px] text-[#FBBF24] flex items-center gap-1.5 bg-[#FBBF241A] border border-[#FBBF2440] rounded-lg px-3 py-2">
            <AlertTriangle size={13} /> Market is closed. This is a preview built on the last close. Entries unlock at the open.
          </div>
        )}
        {d.warnings.map((w, i) => (
          <div key={i} className="text-[12px] text-[#FBBF24] flex items-start gap-1.5"><AlertTriangle size={13} className="mt-0.5 shrink-0" /> {w}</div>
        ))}
        {d.reasons.map((r, i) => (
          <div key={i} className="text-[12px] text-[#8B93A7] flex items-start gap-1.5"><Info size={13} className="mt-0.5 shrink-0" /> {r}</div>
        ))}

        <div className="flex items-center gap-6 flex-wrap pt-1">
          <div className="flex flex-col gap-1 min-w-[160px]">
            <span className="text-[10px] uppercase tracking-wider text-[#5B6478]">Conviction (model score, not a win probability)</span>
            <ConvictionBar value={d.conviction} color={dirColor} />
          </div>
          <Stat label="Composite" value={(d.composite.score >= 0 ? "+" : "") + d.composite.score.toFixed(2)} sub={`${Math.round(d.composite.agreement * 100)}% agree · ${d.composite.n_factors} factors`} tone={dirColor} />
          <Stat label="Expected move" value={v.expected_move ? `±$${num(v.expected_move)}` : "–"} sub={v.expected_move_pct ? `±${v.expected_move_pct}% to ${shortDate(d.expiry.date)}` : ""} />
          <Stat label="ATM IV" value={v.atm_iv ? (v.atm_iv * 100).toFixed(0) + "%" : "–"}
            sub={v.iv_rank != null ? `IV rank ${v.iv_rank} (${v.iv_rank_samples}d logged)` : `IV vs realised ${v.iv_vs_hv ?? "–"}x · rank needs ${Math.max(0, 20 - v.iv_rank_samples)} more days`}
            tone={v.iv_expensive ? "#FBBF24" : undefined} />
          <Stat label="Earnings" value={d.earnings.date ? shortDate(String(d.earnings.date).slice(0, 10)) : "none found"} sub={d.earnings.in_window ? "inside expiry window" : d.earnings.days != null ? `${d.earnings.days}d away` : ""} tone={d.earnings.in_window ? "#FBBF24" : undefined} />
        </div>
      </div>

      {canLog && (
        <div className="grid md:grid-cols-2 gap-4">
          <div className={`${CARD} p-4`}>
            <div className="flex items-center gap-2 mb-3"><Target size={14} className="text-[#5EEAD4]" /><h4 className={LABEL}>Entry plan (underlying)</h4></div>
            <div className="grid grid-cols-2 gap-3">
              <Stat label={p.status === "enter_now" ? "Entry zone" : "Wait for"} value={p.status === "enter_now" ? `${p.entry_zone[0]} – ${p.entry_zone[1]}` : p.limit_entry != null ? `≈ ${p.limit_entry}` : "no clean entry"} tone="#5EEAD4" />
              <Stat label="Stop" value={num(p.stop)} sub={`risk $${p.risk_per_share}/sh`} tone="#F87171" />
              <Stat label="Target 1" value={num(p.target1)} sub={`reward $${p.reward_per_share}/sh`} tone="#4ADE80" />
              <Stat label="Target 2 (full move)" value={num(p.target2)} tone="#4ADE80" />
              <Stat label="Reward / risk" value={p.reward_risk.toFixed(1)} tone={p.reward_risk >= 1.5 ? "#4ADE80" : "#FBBF24"} />
            </div>
            <div className="text-[11px] text-[#8B93A7] mt-3">{p.note}. Exit rules: underlying hits stop or target, option loses 50%, or 3 days to expiry.</div>
          </div>

          <div className={`${CARD} p-4`}>
            <div className="flex items-center gap-2 mb-3"><Zap size={14} className="text-[#5EEAD4]" /><h4 className={LABEL}>Contract</h4></div>
            <div className="mono text-[15px] font-semibold mb-3" style={{ color: dirColor }}>{d.ticker} {shortContract(c)} <span className="text-[#5B6478] text-[11px]">({d.expiry.dte}d)</span></div>
            <div className="grid grid-cols-3 gap-3">
              <Stat label="Bid / Ask" value={`${c.bid} / ${c.ask}`} sub={`spread ${c.spread_pct}%`} />
              <Stat label="Assumed fill" value={`$${c.assumed_fill}`} sub={`${usd(c.cost_per_contract)} per contract`} />
              <Stat label="Breakeven" value={num(c.breakeven)} />
              <Stat label="Delta" value={c.delta} />
              <Stat label="Theta / day" value={`$${c.theta_per_day}`} tone="#F87171" />
              <Stat label="PoP (BS model)" value={`${c.pop_pct}%`} sub="at expiry, rough" />
              <Stat label="At target 1" value={c.est_return_at_target_pct != null ? `${c.est_return_at_target_pct > 0 ? "+" : ""}${c.est_return_at_target_pct}%` : "–"} tone="#4ADE80" sub="est. premium, ~5d" />
              <Stat label="At stop" value={c.est_return_at_stop_pct != null ? `${c.est_return_at_stop_pct}%` : "–"} tone="#F87171" />
              <Stat label="Vol / OI" value={`${c.volume} / ${c.oi}`} />
            </div>
            <div className="flex items-center gap-2 mt-4 flex-wrap">
              <span className="text-[11px] text-[#8B93A7]">Contracts</span>
              <input type="number" min="1" max="100" value={contracts} onChange={(e) => setContracts(e.target.value)}
                className="w-16 bg-[#0B0E14] border border-[#232838] rounded-md px-2 py-1 text-[12px] mono text-[#D5D9E0]" />
              <span className="text-[11px] text-[#8B93A7]">My own fill (optional)</span>
              <input type="number" step="0.01" placeholder="auto" value={ownFill} onChange={(e) => setOwnFill(e.target.value)}
                className="w-20 bg-[#0B0E14] border border-[#232838] rounded-md px-2 py-1 text-[12px] mono text-[#D5D9E0]" />
              <button onClick={log} disabled={busy || (!ownFill && (!d.actionable))}
                className="ml-auto text-[12px] font-semibold px-3 py-1.5 rounded-md disabled:opacity-40 disabled:cursor-not-allowed"
                style={{ background: "#5EEAD4", color: "#0B0E14" }}>
                {busy ? "Logging…" : ownFill ? "Log my fill" : "Log paper trade"}
              </button>
            </div>
            {!d.actionable && !ownFill && <div className="text-[11px] text-[#5B6478] mt-2">Auto-fill is disabled right now (see warnings). If you traded this in an outside paper account, type your fill and log it.</div>}
            {msg && <div className="text-[12px] mt-2" style={{ color: msg.ok ? "#4ADE80" : "#F87171" }}>{msg.text}</div>}
          </div>
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        <div className={`${CARD} p-4`}>
          <h4 className={`${LABEL} mb-2`}>Why: factor breakdown</h4>
          {Object.entries(d.factors).map(([k, f]) => <FactorRow key={k} name={k} f={f} contrib={d.composite.contributions[k]} />)}
          <div className="text-[10px] text-[#5B6478] mt-2">Weights renormalise over factors with data. Sentiment is keyword-scored and the policy factor is a hand-written rule list. Both are crude, which is why the journal grades them.</div>
        </div>
        <div className="flex flex-col gap-4">
          <div className={`${CARD} p-4`}>
            <h4 className={`${LABEL} mb-2`}>Price ladder</h4>
            <Ladder d={d} />
          </div>
          <div className={`${CARD} p-4`}>
            <h4 className={`${LABEL} mb-2`}>Unusual options activity</h4>
            <div className="flex gap-5 mb-2">
              <Stat label="Put/Call vol" value={d.flow.put_call.volume ?? "–"} />
              <Stat label="Put/Call OI" value={d.flow.put_call.open_interest ?? "–"} />
              <Stat label="Flow bias" value={(d.flow.bias >= 0 ? "+" : "") + d.flow.bias} tone={d.flow.bias > 0 ? "#4ADE80" : d.flow.bias < 0 ? "#F87171" : undefined} />
            </div>
            {d.flow.items.length === 0 ? <div className="text-[12px] text-[#5B6478]">Nothing trading far above its open interest.</div> : (
              <table className="w-full text-[11px]">
                <thead><tr className="text-[10px] text-[#5B6478] uppercase"><th className="text-left py-1">Contract</th><th className="text-right">Vol/OI</th><th className="text-right">Notional</th><th className="text-right">Side*</th></tr></thead>
                <tbody>
                  {d.flow.items.slice(0, 6).map((f, i) => (
                    <tr key={i} className="border-t border-[#161B27]">
                      <td className="py-1 mono" style={{ color: f.right === "call" ? "#4ADE80" : "#F87171" }}>${f.strike} {f.right === "call" ? "C" : "P"} {shortDate(f.expiry)}</td>
                      <td className="mono text-right">{f.vol_oi ?? "new"}</td>
                      <td className="mono text-right">{usd(f.notional)}</td>
                      <td className="text-right text-[#8B93A7]">{f.side_guess}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="text-[10px] text-[#5B6478] mt-2">*Side is a guess from where the last print sat versus bid/ask. Free data has no real tape.</div>
          </div>
        </div>
      </div>

      {d.expiry.date && <ChainHeatmap apiBase={apiBase} ticker={d.ticker} expirations={d.expirations} defaultExpiry={d.expiry.date} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Board
// ---------------------------------------------------------------------------
export default function OptionsDesk({ apiBase, tickers }) {
  const [view, setView] = useState("board");
  const [list, setList] = useState(() => {
    try { const s = JSON.parse(localStorage.getItem("optionsDeskTickers") || "null"); if (Array.isArray(s) && s.length) return s; } catch { /* ignore */ }
    return tickers;
  });
  const [sigs, setSigs] = useState({});
  const [selected, setSelected] = useState(null);
  const [regime, setRegime] = useState(null);
  const [addValue, setAddValue] = useState("");
  const [onlyActionable, setOnlyActionable] = useState(false);
  const [auto, setAuto] = useState(true);
  const [alerts, setAlerts] = useState(false);
  const [tick, setTick] = useState(0);
  const [journalVersion, setJournalVersion] = useState(0);
  const lastEnter = useRef({});
  const busy = useRef(false);

  useEffect(() => { try { localStorage.setItem("optionsDeskTickers", JSON.stringify(list)); } catch { /* ignore */ } }, [list]);

  const loadOne = useCallback(async (t, refresh = false) => {
    setSigs((s) => ({ ...s, [t]: { ...(s[t] || {}), loading: true } }));
    try {
      const data = await api(apiBase, `/api/options/${t}/signal${refresh ? "?refresh=true" : ""}`, { timeout: 60000 });
      setSigs((s) => ({ ...s, [t]: { data, loading: false, error: null } }));
      const key = data.actionable && data.plan?.status === "enter_now" ? data.direction : null;
      if (key && lastEnter.current[t] !== key && alerts && "Notification" in window && Notification.permission === "granted") {
        new Notification(`${t}: ${DIR_LABEL[data.direction]} setup`, { body: `${shortContract(data.contract)} · conviction ${data.conviction} · stop ${data.plan.stop} · target ${data.plan.target1}` });
      }
      lastEnter.current[t] = key;
    } catch (e) {
      setSigs((s) => ({ ...s, [t]: { ...(s[t] || {}), loading: false, error: e.message } }));
    }
  }, [apiBase, alerts]);

  const loadAll = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      api(apiBase, "/api/options/regime").then(setRegime).catch(() => setRegime(null));
      const queue = [...list];
      await Promise.all(Array.from({ length: 3 }, async () => { while (queue.length) await loadOne(queue.shift()); }));
    } finally { busy.current = false; }
  }, [apiBase, list, loadOne]);

  useEffect(() => { loadAll(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [tick]);
  useEffect(() => {
    if (!auto) return undefined;
    const id = setInterval(() => setTick((x) => x + 1), 2 * 60 * 1000);
    return () => clearInterval(id);
  }, [auto]);
  useEffect(() => { // newly added tickers load on their own
    list.forEach((t) => { if (!sigs[t]) loadOne(t); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list]);

  const rows = useMemo(() => {
    const r = list.map((t) => ({ t, ...(sigs[t] || {}) }));
    const f = onlyActionable ? r.filter((x) => x.data?.actionable) : r;
    return f.sort((a, b) => (b.data?.conviction ?? -1) - (a.data?.conviction ?? -1));
  }, [list, sigs, onlyActionable]);

  const marketOpen = Object.values(sigs).find((s) => s.data)?.data.meta.market_open;
  const enableAlerts = async () => {
    if (!("Notification" in window)) return;
    const perm = await Notification.requestPermission();
    setAlerts(perm === "granted");
  };
  const addTicker = () => {
    const t = addValue.trim().toUpperCase();
    if (t && /^[A-Z.\-^]{1,10}$/.test(t) && !list.includes(t)) setList([...list, t]);
    setAddValue("");
  };
  const sel = selected && sigs[selected]?.data;

  return (
    <div className="max-w-6xl mx-auto px-5 py-6 flex flex-col gap-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-1 bg-[#0F131C] border border-[#1E2330] rounded-lg p-1">
          {[["board", "Signal board"], ["journal", "Paper journal"]].map(([id, label]) => (
            <button key={id} onClick={() => setView(id)} className="text-[12px] font-medium px-3 py-1.5 rounded-md"
              style={{ color: view === id ? "#0B0E14" : "#8B93A7", background: view === id ? "#5EEAD4" : "transparent" }}>{label}</button>
          ))}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {marketOpen != null && (
            <span className="text-[11px] px-2 py-[3px] rounded-full" style={{ color: marketOpen ? "#4ADE80" : "#FBBF24", background: (marketOpen ? "#4ADE80" : "#FBBF24") + "1A" }}>
              {marketOpen ? "US market open" : "US market closed"}
            </span>
          )}
          {regime?.available && (
            <span className="text-[11px] px-2 py-[3px] rounded-full mono" title={regime.detail}
              style={{ color: regime.label === "risk-on" ? "#4ADE80" : regime.label === "risk-off" ? "#F87171" : "#8B93A7", background: "#1E2330" }}>
              {regime.label} · VIX {regime.vix}
            </span>
          )}
          <button onClick={() => setAuto(!auto)} className="text-[11px] px-2.5 py-1 rounded-full border border-[#232838]" style={{ color: auto ? "#5EEAD4" : "#8B93A7" }}>
            Auto-refresh 2m: {auto ? "on" : "off"}
          </button>
          <button onClick={enableAlerts} className="flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-full border border-[#232838]" style={{ color: alerts ? "#5EEAD4" : "#8B93A7" }}>
            <Bell size={11} /> Alerts {alerts ? "on" : "off"}
          </button>
          <button onClick={() => setTick((x) => x + 1)} className="flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-full border border-[#232838] text-[#8B93A7] hover:text-[#E8EAED] hover:border-[#5EEAD4]">
            <RefreshCw size={11} /> Refresh board
          </button>
        </div>
      </div>

      <div className="text-[11px] text-[#5B6478]">Paper trading and education only. Not financial advice. Signals are model output built on delayed free data, and they can be wrong.</div>

      {view === "journal" && <OptionsJournal apiBase={apiBase} version={journalVersion} />}

      {view === "board" && (
        <>
          <div className={`${CARD} overflow-x-auto`}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#1E2330] flex-wrap gap-2">
              <label className="flex items-center gap-2 text-[12px] text-[#8B93A7]">
                <input type="checkbox" checked={onlyActionable} onChange={(e) => setOnlyActionable(e.target.checked)} /> Show only actionable now
              </label>
              <div className="flex items-center gap-2">
                <input value={addValue} onChange={(e) => setAddValue(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addTicker()} placeholder="Add ticker"
                  className="w-28 bg-[#0B0E14] border border-[#232838] rounded-md px-2 py-1 text-[12px] mono text-[#D5D9E0]" />
                <button onClick={addTicker} className="text-[#8B93A7] hover:text-[#5EEAD4]"><Plus size={14} /></button>
              </div>
            </div>
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-[10px] uppercase tracking-wider text-[#5B6478] text-left">
                  <th className="px-4 py-2">Ticker</th><th>Spot</th><th>Signal</th><th>Conviction</th><th>Entry</th><th>Contract</th><th>R:R</th><th>IV</th><th>Flags</th><th>Data</th><th />
                </tr>
              </thead>
              <tbody>
                {rows.map(({ t, data: d, loading, error }) => (
                  <tr key={t} onClick={() => d && setSelected(t)} className="border-t border-[#161B27] cursor-pointer hover:bg-[#131826]"
                    style={selected === t ? { background: "#131826" } : {}}>
                    <td className="px-4 py-2.5 font-semibold display">{t}</td>
                    {d ? (
                      <>
                        <td className="mono">${num(d.spot)}</td>
                        <td><span className="text-[11px] font-bold" style={{ color: DIR_COLOR[d.direction] }}>{DIR_LABEL[d.direction]}</span></td>
                        <td className="pr-4"><ConvictionBar value={d.conviction} color={DIR_COLOR[d.direction]} /></td>
                        <td><EntryBadge d={d} /></td>
                        <td className="mono text-[11px] text-[#D5D9E0]">{d.direction === "no_trade" ? "–" : shortContract(d.contract)}</td>
                        <td className="mono">{d.plan ? d.plan.reward_risk.toFixed(1) : "–"}</td>
                        <td className="mono text-[11px]" style={{ color: d.volatility.iv_expensive ? "#FBBF24" : "#8B93A7" }}>{d.volatility.atm_iv ? (d.volatility.atm_iv * 100).toFixed(0) + "%" : "–"}</td>
                        <td className="text-[10px] text-[#FBBF24]">{d.earnings.in_window ? "EARNINGS " : ""}{d.volatility.iv_expensive ? "RICH-IV" : ""}</td>
                        <td><span className="mono text-[10px]" style={{ color: d.meta.quote.fresh ? "#4ADE80" : "#FBBF24" }}>{fmtAge(d.meta.quote.age_s)}</span></td>
                      </>
                    ) : (
                      <td colSpan={9} className="text-[11px] text-[#5B6478]">{loading ? "Loading…" : error ? <span className="text-[#FBBF24]">{error}</span> : "–"}</td>
                    )}
                    <td className="pr-3 text-right">
                      <button onClick={(e) => { e.stopPropagation(); setList(list.filter((x) => x !== t)); if (selected === t) setSelected(null); }} className="text-[#3B4256] hover:text-[#F87171]"><X size={12} /></button>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={11} className="px-4 py-6 text-center text-[12px] text-[#5B6478]">Nothing actionable right now.</td></tr>}
              </tbody>
            </table>
          </div>

          {sel ? (
            <Detail d={sel} apiBase={apiBase} loading={sigs[selected]?.loading} onRefresh={() => loadOne(selected, true)} onLogged={() => setJournalVersion((v) => v + 1)} />
          ) : (
            <div className="text-[12px] text-[#5B6478] text-center py-6">Pick a ticker to see its entry plan, contract, levels, factor breakdown and chain.</div>
          )}
        </>
      )}
    </div>
  );
}
