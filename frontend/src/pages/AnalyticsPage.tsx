import React, { useCallback, useEffect, useState } from 'react';
import { RefreshCw, Trash2 } from 'lucide-react';
import { addAffiliate, addLedgerEntry, collectAnalytics, deleteAffiliate, deleteLedgerEntry, getAffiliates, getBusinessDashboard, getLedger, refreshInsights, updateBusinessConfig } from '../api';

const money = (value: number | null | undefined, currency: string) => value === null || value === undefined ? '—' :
  new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(value);
const count = (value: number | null | undefined) => value === null || value === undefined ? 'Not available' : value.toLocaleString();

const Card: React.FC<{ title: string; children: React.ReactNode; note?: string }> = ({ title, children, note }) =>
  <section className="bg-white border border-zinc-200 rounded-2xl p-5 shadow-sm space-y-2" aria-label={title}>
    <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">{title}</h2>{children}
    {note && <p className="text-[11px] text-zinc-400 leading-relaxed">{note}</p>}
  </section>;

export const AnalyticsPage: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [ledger, setLedger] = useState<any[]>([]);
  const [affiliates, setAffiliates] = useState<any[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [entry, setEntry] = useState({ kind: 'revenue', category: 'platform', amount: '', note: '' });
  const [affiliate, setAffiliate] = useState({ brand: '', url: '', categories: '', disclosure: 'Affiliate link: I may earn a commission.' });
  const [prices, setPrices] = useState({ input: '', output: '' });

  const load = useCallback(async () => {
    const [dashboard, entries, programs] = await Promise.allSettled([getBusinessDashboard(), getLedger(), getAffiliates()]);
    if (dashboard.status === 'fulfilled') setData(dashboard.value); else setError(dashboard.reason?.message || 'Could not load analytics.');
    if (entries.status === 'fulfilled') setLedger(entries.value.entries);
    if (programs.status === 'fulfilled') setAffiliates(programs.value.affiliates);
  }, []);
  useEffect(() => { load(); }, [load]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true); setError('');
    try { await action(); await load(); } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };
  if (!data) return <div className="flex-1 p-10 text-sm text-zinc-500">{error || 'Loading real channel data…'}</div>;
  const cur = data.currency;
  const categories = entry.kind === 'revenue' ? ['platform', 'sponsorship', 'affiliate', 'other'] : ['cloud_ai', 'video_generation', 'storage', 'software', 'other'];

  return <div className="flex-1 overflow-y-auto p-6 lg:p-10 bg-[#FAF9F6]"><div className="max-w-5xl mx-auto pb-12 space-y-6">
    <div className="flex items-end justify-between gap-4">
      <div><h1 className="text-3xl font-bold tracking-tight">Analytics</h1>
        <p className="text-sm text-zinc-500 mt-2 max-w-xl">Only recorded numbers appear here. Nothing is projected, and revenue is whatever you or a connected integration has entered.</p></div>
      <button disabled={busy} onClick={() => run(collectAnalytics)} className="shrink-0 h-9 px-3 rounded-xl bg-zinc-900 text-white text-xs font-medium flex items-center gap-1.5 disabled:opacity-50"><RefreshCw size={13} className={busy ? 'animate-spin' : ''} />Collect YouTube results</button>
    </div>
    {error && <p role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-xl p-3">{error}</p>}

    <div className="grid md:grid-cols-3 gap-4">
      <Card title="Channel growth" note={data.growth.source}>
        <p className="text-sm">Uploads by ClipRank <b>{data.growth.uploads}</b> · last 90 days <b>{data.growth.uploads_last_90_days}</b></p>
        <p className="text-sm">Views on tracked uploads <b>{count(data.growth.tracked_views)}</b></p>
        <p className="text-sm">Subscribers gained (tracked) <b>{count(data.growth.subscribers_gained_tracked)}</b></p>
        {data.growth.channel && <p className="text-sm">{data.growth.channel.channel}: <b>{count(data.growth.channel.subscribers)}</b> subscribers · <b>{count(data.growth.channel.views)}</b> views</p>}
      </Card>
      <Card title="Monetization status" note={data.monetization.note}>
        <label className="text-xs block">Your status
          <select aria-label="Monetization status" className="creator-select mt-1" value={data.monetization.status} onChange={e => run(() => updateBusinessConfig({ ypp_status: e.target.value }))}>
            <option value="not_applied">Not applied</option><option value="eligible_tracking">Tracking eligibility</option><option value="applied">Applied</option><option value="approved">Approved</option></select></label>
        {data.monetization.tracks.map((t: any) => <div key={t.name} className="text-xs"><p className="font-medium">{t.name}</p>
          <p className="text-zinc-500">{t.subscribers_progress === null ? 'Connect YouTube to track subscribers.' : `${Math.round(t.subscribers_progress * 100)}% of ${t.subscribers.toLocaleString()} subscribers`}{t.public_uploads_90d ? ` · ${t.uploads_ok ? '✓' : '✕'} ${t.public_uploads_90d} public uploads in 90 days` : ''}</p></div>)}
      </Card>
      <Card title="Net" note={data.net.formula}>
        <p className="text-3xl font-bold">{money(data.net.amount, cur)}</p>
        <p className="text-xs text-zinc-500">Revenue {money(data.revenue.total, cur)} − costs {money(data.costs.recorded, cur)}</p>
      </Card>
    </div>

    <div className="grid md:grid-cols-3 gap-4">
      <Card title="Revenue" note={data.revenue.note}><p className="text-2xl font-bold">{money(data.revenue.total, cur)}</p></Card>
      <Card title="Affiliate revenue"><p className="text-2xl font-bold">{money(data.affiliate.revenue, cur)}</p><p className="text-xs text-zinc-500">{data.affiliate.programs} configured program(s)</p></Card>
      <Card title="Sponsorship" note="Tracked manually: add each payment below."><p className="text-2xl font-bold">{money(data.sponsorship.revenue, cur)}</p></Card>
    </div>

    <Card title="Costs" note={data.costs.cloud_ai.note}>
      <p className="text-sm">Recorded operating costs <b>{money(data.costs.recorded, cur)}</b> (ledger {money(data.costs.ledger, cur)}{data.costs.cloud_ai.priced ? ` + cloud AI ${money(data.costs.cloud_ai.estimated_cost, cur)}` : ''})</p>
      <p className="text-xs text-zinc-500">Cloud AI so far: {data.costs.cloud_ai.calls.toLocaleString()} calls · {data.costs.cloud_ai.input_tokens.toLocaleString()} input / {data.costs.cloud_ai.output_tokens.toLocaleString()} output tokens</p>
      <div className="flex flex-wrap gap-2 items-end text-xs">
        <label>Input price / 1M tokens<input aria-label="Input token price" className="creator-select mt-1" value={prices.input} onChange={e => setPrices({ ...prices, input: e.target.value })} placeholder="e.g. 0.10" /></label>
        <label>Output price / 1M tokens<input aria-label="Output token price" className="creator-select mt-1" value={prices.output} onChange={e => setPrices({ ...prices, output: e.target.value })} placeholder="e.g. 0.40" /></label>
        <button disabled={busy || !prices.input || !prices.output} onClick={() => run(() => updateBusinessConfig({ ai_input_price_per_million: Number(prices.input), ai_output_price_per_million: Number(prices.output) }))} className="h-9 px-3 rounded-xl border border-zinc-300 font-medium">Save prices</button>
      </div>
    </Card>

    <Card title="Record revenue or a cost">
      <form className="grid sm:grid-cols-[110px_150px_120px_1fr_auto] gap-2 items-end text-xs" onSubmit={e => { e.preventDefault(); run(async () => { await addLedgerEntry({ kind: entry.kind, category: entry.category, amount: Number(entry.amount), note: entry.note }); setEntry({ ...entry, amount: '', note: '' }); }); }}>
        <label>Type<select aria-label="Entry type" className="creator-select mt-1" value={entry.kind} onChange={e => setEntry({ ...entry, kind: e.target.value, category: e.target.value === 'revenue' ? 'platform' : 'cloud_ai' })}><option value="revenue">Revenue</option><option value="cost">Cost</option></select></label>
        <label>Category<select aria-label="Entry category" className="creator-select mt-1" value={entry.category} onChange={e => setEntry({ ...entry, category: e.target.value })}>{categories.map(c => <option key={c} value={c}>{c.replace('_', ' ')}</option>)}</select></label>
        <label>Amount ({cur})<input aria-label="Entry amount" type="number" min="0" step="0.01" className="creator-select mt-1" value={entry.amount} onChange={e => setEntry({ ...entry, amount: e.target.value })} required /></label>
        <label>Source / note<input aria-label="Entry note" className="creator-select mt-1" value={entry.note} onChange={e => setEntry({ ...entry, note: e.target.value })} placeholder="Where did this figure come from?" required minLength={3} /></label>
        <button disabled={busy} className="h-9 px-4 rounded-xl bg-zinc-900 text-white font-medium">Add</button>
      </form>
      {ledger.length > 0 && <ul className="divide-y divide-zinc-100 text-xs mt-2">{ledger.slice(0, 12).map(row => <li key={row.id} className="py-2 flex items-center gap-3">
        <span className="w-20 text-zinc-400">{row.occurred_on}</span><span className={`w-16 font-medium ${row.kind === 'revenue' ? 'text-emerald-700' : 'text-red-700'}`}>{row.kind}</span>
        <span className="w-24 text-zinc-500">{row.category.replace('_', ' ')}</span><span className="w-24 font-semibold">{money(row.amount, row.currency)}</span><span className="flex-1 text-zinc-600 truncate">{row.note}</span>
        <button aria-label={`Delete entry ${row.id}`} onClick={() => run(() => deleteLedgerEntry(row.id))} className="text-zinc-400 hover:text-red-600"><Trash2 size={13} /></button></li>)}</ul>}
    </Card>

    <Card title="Affiliate programs" note="A link is added to a Short's description only when its content mentions one of the program's topics. Unrelated entertainment never gets an offer.">
      <form className="grid sm:grid-cols-2 gap-2 text-xs" onSubmit={e => { e.preventDefault(); run(async () => { await addAffiliate({ brand: affiliate.brand, url: affiliate.url, categories: affiliate.categories.split(',').map(s => s.trim()).filter(Boolean), disclosure: affiliate.disclosure }); setAffiliate({ ...affiliate, brand: '', url: '', categories: '' }); }); }}>
        <label>Brand<input aria-label="Affiliate brand" className="creator-select mt-1" value={affiliate.brand} onChange={e => setAffiliate({ ...affiliate, brand: e.target.value })} required /></label>
        <label>Approved link (https)<input aria-label="Affiliate link" className="creator-select mt-1" value={affiliate.url} onChange={e => setAffiliate({ ...affiliate, url: e.target.value })} placeholder="https://" required /></label>
        <label>Topics it genuinely relates to (comma separated)<input aria-label="Affiliate topics" className="creator-select mt-1" value={affiliate.categories} onChange={e => setAffiliate({ ...affiliate, categories: e.target.value })} placeholder="parkour shoes, climbing chalk" required /></label>
        <label>Disclosure shown to viewers<input aria-label="Affiliate disclosure" className="creator-select mt-1" value={affiliate.disclosure} onChange={e => setAffiliate({ ...affiliate, disclosure: e.target.value })} required /></label>
        <button disabled={busy} className="h-9 px-4 rounded-xl border border-zinc-300 font-medium sm:col-span-2">Add affiliate program</button>
      </form>
      {affiliates.map(a => <p key={a.id} className="text-xs flex justify-between border-t border-zinc-100 pt-2"><span><b>{a.brand}</b> · {a.categories.join(', ')}</span><button onClick={() => run(() => deleteAffiliate(a.id))} className="text-zinc-400 hover:text-red-600">Remove</button></p>)}
    </Card>

    <Card title="What the results are teaching" note="Needs at least 8 videos with measured results and 3 per pattern. Medians, outlier caps and shrinkage stop one lucky video from changing anything. Adjustments are advisory weights, each with the numbers behind it.">
      <button disabled={busy} onClick={() => run(refreshInsights)} className="h-8 px-3 rounded-xl border border-zinc-300 text-xs font-medium">Re-analyze results</button>
      {data.insights.length === 0 ? <p className="text-xs text-zinc-500">No pattern is established yet; strategy is unchanged.</p> :
        <ul className="text-xs space-y-2">{data.insights.map((a: any, i: number) => <li key={i} className="border-t border-zinc-100 pt-2"><b className={a.weight > 0 ? 'text-emerald-700' : 'text-red-700'}>{a.weight > 0 ? '+' : ''}{Math.round(a.weight * 100)}%</b> {a.reason}</li>)}</ul>}
    </Card>
  </div></div>;
};
