import React, { useState, useEffect, useCallback, useRef } from "react";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell, LineChart, Line, CartesianGrid, ReferenceLine } from "recharts";
import { RefreshCw, AlertTriangle, Zap } from "lucide-react";
import { DIR_COLOR, usd, num, shortDate, fmtAge, ageFrom, api } from "./optionsUtil.js";

const CARD = "bg-[#0F131C] border border-[#1E2330] rounded-lg";
const LABEL = "text-[11px] uppercase tracking-wider text-[#8B93A7]";
const REASON = { target: "Target hit", stop: "Stop hit", premium_stop: "Option -50%", time_exit: "Time exit", expired: "Expired", manual: "Manual" };

function Card({ label, value, sub, tone }) {
  return (
    <div className={`${CARD} p-3.5`}>
      <div className="text-[10px] uppercase tracking-wider text-[#5B6478]">{label}</div>
      <div className="mono text-[18px] font-semibold mt-0.5" style={{ color: tone || "#E8EAED" }}>{value}</div>
      {sub && <div className="text-[10px] text-[#5B6478] mt-0.5">{sub}</div>}
    </div>
  );
}

const pnlTone = (n) => (n == null ? undefined : n > 0 ? "#4ADE80" : n < 0 ? "#F87171" : undefined);

export default function OptionsJournal({ apiBase, version }) {
  const [trades, setTrades] = useState([]);
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState(null);
  const [exitFill, setExitFill] = useState({});
  const [checkedAt, setCheckedAt] = useState(null);
  const busy = useRef(false);

  const load = useCallback(async ({ evaluate = false } = {}) => {
    if (busy.current) return;
    busy.current = true;
    setLoading(true); setError(null);
    try {
      if (evaluate) {
        const ev = await api(apiBase, "/api/options/trades/evaluate", { method: "POST", timeout: 90000 });
        setCheckedAt(ev.checked_at);
        const closed = ev.actions.filter((a) => a.reason);
        if (closed.length) setNotice(closed.map((a) => `${a.ticker} closed (${REASON[a.reason] || a.reason}): ${usd(a.pnl_usd, 2)}`).join(" · "));
      }
      const [t, s] = await Promise.all([api(apiBase, "/api/options/trades"), api(apiBase, "/api/options/stats")]);
      setTrades(t.trades); setStats(s);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); busy.current = false; }
  }, [apiBase]);

  useEffect(() => { load({ evaluate: true }); }, [load, version]);
  useEffect(() => {
    const id = setInterval(() => load({ evaluate: true }), 3 * 60 * 1000);
    return () => clearInterval(id);
  }, [load]);

  const close = async (t) => {
    const own = exitFill[t.id];
    try {
      await api(apiBase, `/api/options/trades/${t.id}/close`, { method: "POST", body: JSON.stringify({ exit_premium: own ? Number(own) : null }) });
      setNotice(null); load();
    } catch (e) { setError(e.message); }
  };

  const open = trades.filter((t) => t.status === "open");
  const closed = trades.filter((t) => t.status === "closed");
  const unreal = open.reduce((sum, t) => sum + (t.live?.pnl_usd || 0), 0);
  const calib = (stats?.calibration || []).map((b) => ({ ...b, win: b.win_rate ?? 0 }));

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2"><Zap size={15} className="text-[#5EEAD4]" /><h3 className="text-[13px] font-semibold uppercase tracking-wider text-[#8B93A7]">Options paper journal</h3></div>
        <div className="flex items-center gap-3">
          {checkedAt && <span className="text-[10px] text-[#5B6478]">exit rules last checked {fmtAge(ageFrom(checkedAt))} ago</span>}
          <button onClick={() => load({ evaluate: true })} disabled={loading}
            className="flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-full border border-[#232838] text-[#8B93A7] hover:border-[#5EEAD4] hover:text-[#E8EAED] disabled:opacity-50">
            <RefreshCw size={11} style={loading ? { animation: "spin 0.8s linear infinite" } : {}} /> Check exits now
          </button>
        </div>
      </div>

      {error && <div className="text-[12px] text-[#FBBF24] flex items-center gap-1.5 bg-[#FBBF241A] border border-[#FBBF2440] rounded-lg px-3 py-2"><AlertTriangle size={13} /> {error}</div>}
      {notice && <div className="text-[12px] text-[#5EEAD4] bg-[#5EEAD41A] border border-[#5EEAD440] rounded-lg px-3 py-2">{notice}</div>}

      {stats && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <Card label="Closed trades" value={stats.n} sub={`${stats.open_positions} open`} />
            <Card label="Win rate" value={stats.win_rate != null ? stats.win_rate + "%" : "–"} sub={stats.avg_win_pct != null ? `avg win ${stats.avg_win_pct}% / loss ${stats.avg_loss_pct ?? "–"}%` : ""} />
            <Card label="Realised P&L" value={usd(stats.total_pnl_usd, 2)} tone={pnlTone(stats.total_pnl_usd)} sub={stats.expectancy_usd != null ? `${usd(stats.expectancy_usd, 2)} per trade` : ""} />
            <Card label="Profit factor" value={stats.profit_factor ?? "–"} sub="gross wins / gross losses" tone={stats.profit_factor > 1 ? "#4ADE80" : stats.profit_factor != null ? "#F87171" : undefined} />
            <Card label="Open P&L (live)" value={usd(unreal, 2)} tone={pnlTone(unreal)} sub={`${usd(stats.open_premium_at_risk_usd)} premium at risk`} />
          </div>
          <div className="text-[11px] text-[#FBBF24]">{stats.sample_note}</div>
        </>
      )}

      <div className={`${CARD} overflow-x-auto`}>
        <div className="px-4 py-3 border-b border-[#1E2330]"><h4 className={LABEL}>Open positions</h4></div>
        <table className="w-full text-[12px]">
          <thead><tr className="text-[10px] uppercase tracking-wider text-[#5B6478] text-left"><th className="px-4 py-2">Contract</th><th>Qty</th><th>Entry</th><th>Mark</th><th>P&L</th><th>Stop / Target</th><th>Conv.</th><th>Source</th><th>My exit fill</th><th /></tr></thead>
          <tbody>
            {open.map((t) => (
              <tr key={t.id} className="border-t border-[#161B27]">
                <td className="px-4 py-2 mono" style={{ color: DIR_COLOR[t.right] }}>{t.ticker} ${t.strike} {t.right === "call" ? "C" : "P"} {shortDate(String(t.expiry))}</td>
                <td className="mono">{t.contracts}</td>
                <td className="mono">${num(t.entry_premium)}</td>
                <td className="mono">{t.live?.mark != null ? `$${num(t.live.mark)}` : <span className="text-[#5B6478]" title={t.live?.error}>n/a</span>}</td>
                <td className="mono" style={{ color: pnlTone(t.live?.pnl_usd) }}>{t.live?.pnl_usd != null ? `${usd(t.live.pnl_usd, 2)} (${t.live.pnl_pct}%)` : "–"}</td>
                <td className="mono text-[11px] text-[#8B93A7]">{t.stop_underlying ?? "–"} / {t.target_underlying ?? "–"}</td>
                <td className="mono">{t.conviction ?? "–"}</td>
                <td className="text-[11px] text-[#8B93A7]">{t.source}</td>
                <td><input type="number" step="0.01" placeholder="auto" value={exitFill[t.id] || ""} onChange={(e) => setExitFill({ ...exitFill, [t.id]: e.target.value })}
                  className="w-16 bg-[#0B0E14] border border-[#232838] rounded-md px-1.5 py-0.5 text-[11px] mono text-[#D5D9E0]" /></td>
                <td className="pr-3 text-right"><button onClick={() => close(t)} className="text-[11px] px-2 py-1 rounded-md border border-[#232838] text-[#8B93A7] hover:text-[#F87171] hover:border-[#F87171]">Close</button></td>
              </tr>
            ))}
            {open.length === 0 && <tr><td colSpan={10} className="px-4 py-5 text-center text-[12px] text-[#5B6478]">No open positions. Log one from the signal board.</td></tr>}
          </tbody>
        </table>
      </div>

      {stats && stats.n > 0 && (
        <div className="grid md:grid-cols-2 gap-4">
          <div className={`${CARD} p-4`}>
            <h4 className={`${LABEL} mb-1`}>Calibration: win rate by conviction</h4>
            <div className="text-[10px] text-[#5B6478] mb-2">If conviction means anything, bars should climb left to right. Trade counts shown under each bucket.</div>
            <div style={{ height: 190 }}>
              <ResponsiveContainer>
                <BarChart data={calib.map((b) => ({ ...b, label: `${b.bucket} (n=${b.n})` }))}>
                  <XAxis dataKey="label" tick={{ fill: "#8B93A7", fontSize: 10 }} axisLine={false} tickLine={false} />
                  <YAxis domain={[0, 100]} tick={{ fill: "#5B6478", fontSize: 10 }} axisLine={false} tickLine={false} width={28} />
                  <ReferenceLine y={50} stroke="#3B4256" strokeDasharray="3 3" />
                  <Tooltip contentStyle={{ background: "#0F131C", border: "1px solid #232838", fontSize: 11 }} formatter={(v, n, p) => [`${v}% win, avg ${p.payload.avg_pnl_pct ?? "–"}%`, "Bucket"]} />
                  <Bar dataKey="win" radius={[4, 4, 0, 0]}>{calib.map((b, i) => <Cell key={i} fill={b.n ? "#5EEAD4" : "#232838"} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div className={`${CARD} p-4`}>
            <h4 className={`${LABEL} mb-1`}>Equity curve (realised, USD)</h4>
            <div className="text-[10px] text-[#5B6478] mb-2">Cumulative P&L of closed trades, in the order they closed.</div>
            <div style={{ height: 190 }}>
              <ResponsiveContainer>
                <LineChart data={stats.equity.map((e, i) => ({ i: i + 1, pnl: e.pnl }))}>
                  <CartesianGrid stroke="#161B27" vertical={false} />
                  <XAxis dataKey="i" tick={{ fill: "#5B6478", fontSize: 10 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "#5B6478", fontSize: 10 }} axisLine={false} tickLine={false} width={44} />
                  <ReferenceLine y={0} stroke="#3B4256" />
                  <Tooltip contentStyle={{ background: "#0F131C", border: "1px solid #232838", fontSize: 11 }} formatter={(v) => [usd(v, 2), "Cumulative"]} labelFormatter={(l) => `Trade ${l}`} />
                  <Line type="monotone" dataKey="pnl" stroke="#5EEAD4" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div className={`${CARD} p-4 md:col-span-2 overflow-x-auto`}>
            <h4 className={`${LABEL} mb-1`}>Which factors earn their weight?</h4>
            <div className="text-[10px] text-[#5B6478] mb-2">For each factor: trades where it pointed the same way as the trade (score of 0.2 or more) versus the rest. If "aligned" does not beat "not aligned" after 30+ trades, cut its weight.</div>
            <table className="w-full text-[12px]">
              <thead><tr className="text-[10px] uppercase tracking-wider text-[#5B6478] text-left"><th className="py-1">Factor</th><th>Aligned: n</th><th>Win rate</th><th>Avg P&L</th><th>Not aligned: n</th><th>Win rate</th><th>Avg P&L</th></tr></thead>
              <tbody>
                {Object.entries(stats.factors).map(([k, f]) => (
                  <tr key={k} className="border-t border-[#161B27]">
                    <td className="py-1.5 text-[#D5D9E0]">{k}</td>
                    <td className="mono">{f.aligned.n}</td><td className="mono">{f.aligned.win_rate ?? "–"}{f.aligned.win_rate != null ? "%" : ""}</td><td className="mono" style={{ color: pnlTone(f.aligned.avg_pnl_pct) }}>{f.aligned.avg_pnl_pct ?? "–"}{f.aligned.avg_pnl_pct != null ? "%" : ""}</td>
                    <td className="mono">{f.not_aligned.n}</td><td className="mono">{f.not_aligned.win_rate ?? "–"}{f.not_aligned.win_rate != null ? "%" : ""}</td><td className="mono" style={{ color: pnlTone(f.not_aligned.avg_pnl_pct) }}>{f.not_aligned.avg_pnl_pct ?? "–"}{f.not_aligned.avg_pnl_pct != null ? "%" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className={`${CARD} overflow-x-auto`}>
        <div className="px-4 py-3 border-b border-[#1E2330]"><h4 className={LABEL}>Closed trades</h4></div>
        <table className="w-full text-[12px]">
          <thead><tr className="text-[10px] uppercase tracking-wider text-[#5B6478] text-left"><th className="px-4 py-2">Contract</th><th>Qty</th><th>Entry → Exit</th><th>P&L</th><th>Reason</th><th>Conv.</th><th>Source</th><th>Closed</th></tr></thead>
          <tbody>
            {closed.map((t) => (
              <tr key={t.id} className="border-t border-[#161B27]">
                <td className="px-4 py-2 mono" style={{ color: DIR_COLOR[t.right] }}>{t.ticker} ${t.strike} {t.right === "call" ? "C" : "P"} {shortDate(String(t.expiry))}</td>
                <td className="mono">{t.contracts}</td>
                <td className="mono">${num(t.entry_premium)} → ${num(t.exit_premium)}</td>
                <td className="mono" style={{ color: pnlTone(t.pnl_usd) }}>{usd(t.pnl_usd, 2)} ({t.pnl_pct}%)</td>
                <td className="text-[11px] text-[#8B93A7]">{REASON[t.exit_reason] || t.exit_reason}</td>
                <td className="mono">{t.conviction ?? "–"}</td>
                <td className="text-[11px] text-[#8B93A7]">{t.source}</td>
                <td className="text-[11px] text-[#5B6478]">{t.exit_at ? new Date(t.exit_at).toLocaleDateString() : ""}</td>
              </tr>
            ))}
            {closed.length === 0 && <tr><td colSpan={8} className="px-4 py-5 text-center text-[12px] text-[#5B6478]">Closed trades will appear here once an exit rule fires or you close one.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="text-[10px] text-[#5B6478]">Fills assume you pay a quarter of the bid/ask spread on entry and give up a quarter on exit. Exit rules run while this page is open and when you press "Check exits now". Positions are not watched while the app is closed.</div>
    </div>
  );
}
