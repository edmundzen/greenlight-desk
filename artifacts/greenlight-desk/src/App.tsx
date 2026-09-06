import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, ArrowUpRight, Check, CheckCircle2, ChevronRight, Clock3, FileText, Film, FolderOpen, Loader2, Menu, MoreHorizontal, Plus, RefreshCw, Search, ShieldCheck, Sparkles, Upload, X, XCircle } from 'lucide-react';
import { useAnalyzeScreenplay, useDecideScreenplay, useGetScreenplay, useListScreenplays, useRestartScreenplay, useRetryScreenplayKeyArt, getGetScreenplayQueryKey, getListScreenplaysQueryKey } from '@workspace/api-client-react';
import type { DecisionInput, ScreenplayDetail, ScreenplaySummary, TraceEvent } from '@workspace/api-client-react';
import { ErrorBoundary } from '@/components/error-boundary';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import NotFound from '@/pages/not-found';
import { Route, Switch, useLocation, Router as WouterRouter } from 'wouter';
import './index.css';

const queryClient = new QueryClient();

const formatDate = (value?: string | null) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(date);
};

const formatTime = (value?: string | null) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' }).format(date);
};

function StatusPill({ status }: { status: string }) {
  const labels: Record<string, string> = { queued: 'Queued', analyzing: 'Analyzing', ready: 'Ready for review', approved: 'Approved', rejected: 'Rejected', failed: 'Needs attention' };
  const isWarm = status === 'approved' || status === 'ready';
  const isBad = status === 'rejected' || status === 'failed';
  return (
    <span data-testid={`status-pill-${status}`} className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold tracking-wide ${isWarm ? 'border-[#c9dacf] bg-[#e8f0e8] text-[#315846]' : isBad ? 'border-[#e4c7c0] bg-[#f7e9e5] text-[#9a493b]' : 'border-[#ddd5c7] bg-[#eee9df] text-[#786f62]'}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${isWarm ? 'bg-[#4b8061]' : isBad ? 'bg-[#bd6252]' : 'bg-[#a79d8c]'}`} />
      {labels[status] ?? status}
    </span>
  );
}

function Recommendation({ value }: { value?: string | null }) {
  if (!value) return <span className="text-xs text-muted-foreground">Pending recommendation</span>;
  const tones: Record<string, string> = { Recommend: 'text-[#2e6549]', Consider: 'text-[#9b6328]', Pass: 'text-[#9a493b]' };
  return <span className={`text-xs font-semibold ${tones[value] ?? 'text-foreground'}`}>{value}</span>;
}

function LogoMark() {
  return (
    <div className="relative flex h-9 w-9 items-center justify-center rounded-[10px] bg-primary text-primary-foreground shadow-[0_5px_14px_rgba(164,75,48,.18)]">
      <Film size={19} strokeWidth={1.7} />
      <span className="absolute -right-1 -top-1 h-2 w-2 rounded-full border-2 border-sidebar bg-accent" />
    </div>
  );
}

function Rail({ screenplays, selectedId, onSelect, onUpload, onRefresh, isRefreshing }: { screenplays: ScreenplaySummary[]; selectedId?: string; onSelect: (id: string) => void; onUpload: () => void; onRefresh: () => void; isRefreshing: boolean }) {
  const [search, setSearch] = useState('');
  const filtered = useMemo(() => screenplays.filter((item) => item.fileName.toLowerCase().includes(search.toLowerCase())), [screenplays, search]);
  return (
    <aside className="flex min-h-0 w-full flex-col bg-sidebar text-sidebar-foreground md:w-[286px] md:flex-none">
      <div className="border-b border-sidebar-border px-5 pb-5 pt-6">
        <div className="flex items-center gap-3">
          <LogoMark />
          <div>
            <div className="text-[15px] font-bold tracking-[-0.02em]">Greenlight Desk</div>
            <div className="font-data mt-0.5 text-[9px] uppercase tracking-[0.18em] text-sidebar-foreground/55">Producer coverage</div>
          </div>
        </div>
      </div>
      <div className="flex items-center justify-between px-5 pb-3 pt-6">
        <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-sidebar-foreground/50">Recent screenplays</div>
        <button data-testid="button-refresh-screenplays" onClick={onRefresh} className="rounded-md p-1.5 text-sidebar-foreground/55 transition hover:bg-sidebar-accent hover:text-sidebar-foreground" aria-label="Refresh screenplays">
          <RefreshCw size={14} className={isRefreshing ? 'animate-spin' : ''} />
        </button>
      </div>
      <div className="relative mx-4 mb-3">
        <Search size={14} className="absolute left-3 top-2.5 text-sidebar-foreground/40" />
        <input data-testid="input-search-screenplays" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find a screenplay" className="h-9 w-full rounded-md border border-sidebar-border bg-sidebar-accent/60 pl-9 pr-3 text-xs text-sidebar-foreground outline-none placeholder:text-sidebar-foreground/35 focus:border-sidebar-primary" />
      </div>
      <div className="min-h-[120px] flex-1 overflow-y-auto px-3 pb-4">
        {filtered.length ? filtered.map((item, index) => (
          <button data-testid={`button-select-screenplay-${item.id}`} key={item.id} onClick={() => onSelect(item.id)} className={`group mb-1 w-full rounded-lg border px-3 py-3 text-left transition ${selectedId === item.id ? 'border-sidebar-border bg-sidebar-accent' : 'border-transparent hover:border-sidebar-border hover:bg-sidebar-accent/60'}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="flex min-w-0 items-start gap-2.5">
                <FileText size={15} className={`mt-0.5 flex-none ${selectedId === item.id ? 'text-[#e49a7f]' : 'text-sidebar-foreground/45'}`} />
                <div className="min-w-0">
                  <div data-testid={`text-screenplay-name-${item.id}`} className="truncate text-[13px] font-medium">{item.fileName}</div>
                  <div className="mt-1 flex items-center gap-2 text-[10px] text-sidebar-foreground/45"><span>{formatDate(item.createdAt)}</span><span className="h-0.5 w-0.5 rounded-full bg-sidebar-foreground/35" /><Recommendation value={item.recommendation} /></div>
                </div>
              </div>
              {selectedId === item.id ? <ChevronRight size={14} className="mt-0.5 flex-none text-[#e49a7f]" /> : <StatusPill status={item.status} />}
            </div>
          </button>
        )) : (
          <div data-testid="empty-search-screenplays" className="px-3 py-8 text-center text-xs text-sidebar-foreground/45">No matching screenplays.</div>
        )}
      </div>
      <div className="border-t border-sidebar-border p-4">
        <button data-testid="button-upload-screenplay" onClick={onUpload} className="flex h-10 w-full items-center justify-center gap-2 rounded-md bg-sidebar-primary text-[12px] font-semibold text-sidebar-primary-foreground transition hover:brightness-110 active:scale-[.98]">
          <Plus size={15} /> Upload screenplay
        </button>
        <div className="mt-4 flex items-center gap-2 px-1 text-[10px] text-sidebar-foreground/45"><ShieldCheck size={13} /><span>Private by default</span></div>
      </div>
    </aside>
  );
}

function WorkspaceHeader({ detail, onMenu }: { detail?: ScreenplayDetail; onMenu: () => void }) {
  return (
    <header className="flex min-h-[76px] items-center justify-between border-b border-border/80 bg-card/70 px-5 md:px-8">
      <div className="flex min-w-0 items-center gap-3">
        <button data-testid="button-open-rail" onClick={onMenu} className="rounded-md p-2 text-muted-foreground hover:bg-muted md:hidden" aria-label="Open screenplay list"><Menu size={18} /></button>
        <div className="min-w-0">
          <div className="font-data mb-1 text-[9px] uppercase tracking-[0.19em] text-muted-foreground">Coverage workspace <span className="px-1 text-border">/</span> selected script</div>
          <h1 data-testid="text-selected-screenplay" className="truncate text-lg font-semibold tracking-[-0.03em] md:text-[21px]">{detail?.fileName ?? 'Choose a screenplay to begin'}</h1>
        </div>
      </div>
      <div className="flex items-center gap-3">
        {detail && <StatusPill status={detail.status} />}
        <div className="hidden h-8 w-8 items-center justify-center rounded-full border border-border bg-muted text-xs font-semibold text-muted-foreground sm:flex">AM</div>
      </div>
    </header>
  );
}

function Trace({ trace, status }: { trace: TraceEvent[]; status: string }) {
  const fallback = status === 'analyzing' || status === 'queued';
  return (
    <section className="rounded-xl border border-border bg-card p-5 shadow-[0_8px_24px_rgba(77,61,40,.035)]">
      <div className="mb-5 flex items-start justify-between">
        <div><div className="font-data text-[9px] uppercase tracking-[0.18em] text-muted-foreground">Agent trace</div><h2 className="mt-1 text-[17px] font-semibold tracking-[-0.02em]">Reading the room</h2></div>
        <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground"><span className={`h-1.5 w-1.5 rounded-full ${fallback ? 'animate-pulse bg-primary' : 'bg-[#558266]'}`} />{fallback ? 'Live' : 'Complete'}</div>
      </div>
      {trace.length ? (
        <div className="relative ml-1">
          <div className="absolute bottom-3 left-[7px] top-2 w-px bg-border" />
          <div className="space-y-5">
            {trace.map((event) => <TraceRow key={event.id} event={event} />)}
          </div>
        </div>
      ) : (
        <div data-testid="empty-trace" className="rounded-lg border border-dashed border-border bg-muted/40 px-4 py-5 text-xs leading-relaxed text-muted-foreground">Your agent will leave a clear trail here as it reads pages, maps characters, and pressure-tests the story.</div>
      )}
    </section>
  );
}

function TraceRow({ event }: { event: TraceEvent }) {
  const complete = event.status === 'complete';
  const active = event.status === 'active';
  const error = event.status === 'error';
  return (
    <div data-testid={`trace-event-${event.id}`} className="relative flex gap-3">
      <div className={`relative z-10 mt-0.5 flex h-4 w-4 flex-none items-center justify-center rounded-full border-2 ${complete ? 'border-[#639477] bg-[#e8f0e8] text-[#477458]' : active ? 'border-primary bg-[#f8e9e3] text-primary' : error ? 'border-destructive bg-[#f7e9e5] text-destructive' : 'border-border bg-card text-muted-foreground'}`}>
        {complete ? <Check size={9} strokeWidth={3} /> : active ? <span className="h-1.5 w-1.5 rounded-full bg-primary" /> : error ? <X size={9} /> : <span className="h-1.5 w-1.5 rounded-full bg-border" />}
      </div>
      <div className="-mt-0.5 min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2"><div className={`text-[12px] font-semibold ${active ? 'text-primary' : ''}`}>{event.label}</div><div className="font-data flex-none text-[9px] text-muted-foreground">{formatTime(event.createdAt)}</div></div>
        <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{event.detail}</div>
      </div>
    </div>
  );
}

function openKeyArt(url: string) {
  if (!url.startsWith('data:')) {
    window.open(url, '_blank', 'noopener,noreferrer');
    return;
  }
  const comma = url.indexOf(',');
  const metadata = url.slice(5, comma);
  const payload = url.slice(comma + 1);
  const mimeType = metadata.split(';')[0] || 'image/svg+xml';
  const binary = metadata.includes(';base64') ? atob(payload) : decodeURIComponent(payload);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  const objectUrl = URL.createObjectURL(new Blob([bytes], { type: mimeType }));
  window.open(objectUrl, '_blank', 'noopener,noreferrer');
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

function KeyArt({ detail, onRetry, isRetrying, retryError }: { detail?: ScreenplayDetail; onRetry: () => void; isRetrying: boolean; retryError: boolean }) {
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card shadow-[0_8px_24px_rgba(77,61,40,.035)]">
      <div className="flex items-center justify-between border-b border-border px-5 py-4"><div><div className="font-data text-[9px] uppercase tracking-[0.18em] text-muted-foreground">Visual north star</div><h2 className="mt-1 text-[17px] font-semibold tracking-[-0.02em]">Key art</h2></div><Sparkles size={16} className="text-primary" /></div>
      <div className="relative aspect-[1.48/1] overflow-hidden bg-[#1d312e]">
        {detail?.keyArtUrl ? <img data-testid="img-key-art" src={detail.keyArtUrl} alt={`Key art for ${detail.fileName}`} className="h-full w-full object-cover transition duration-700 hover:scale-[1.025]" /> : (
          <div data-testid="empty-key-art" className="absolute inset-0 overflow-hidden bg-[radial-gradient(circle_at_68%_25%,#b66a50_0%,transparent_29%),linear-gradient(135deg,#233e39_0%,#172723_100%)]">
            <div className="absolute -right-8 -top-10 h-48 w-48 rounded-full border border-[#dda083]/30" /><div className="absolute -right-1 top-0 h-36 w-36 rounded-full border border-[#dda083]/20" />
            <div className="absolute bottom-0 left-0 h-[72%] w-[47%] bg-[#df9b79]/10 [clip-path:polygon(0_44%,45%_0,100%_100%,0%_100%)]" />
            <div className="absolute bottom-6 left-6 right-6 flex items-end justify-between"><div><div className="font-data text-[8px] uppercase tracking-[0.22em] text-[#d49b83]">Awaiting image direction</div><div className="mt-2 font-editorial text-3xl italic text-[#f1ddc6]">A story<br />takes shape.</div></div><div className="h-10 w-7 border border-[#d49b83]/50 p-1"><div className="h-full border border-[#d49b83]/40" /></div></div>
          </div>
        )}
      </div>
      <div className="flex items-center justify-between gap-3 px-5 py-3.5">
        <div>
          <span className="text-[11px] text-muted-foreground">{detail?.keyArtUrl ? 'Algorithmically rendered from coverage' : 'Generated after analysis completes'}</span>
          {retryError && <div data-testid="text-key-art-retry-error" className="mt-1 text-[10px] text-destructive">Key art could not be rendered. Coverage is unchanged.</div>}
        </div>
        {detail?.keyArtUrl ? <button data-testid="button-open-key-art" onClick={() => openKeyArt(detail.keyArtUrl ?? '')} className="flex items-center gap-1 text-[11px] font-semibold text-primary hover:underline">Open image <ArrowUpRight size={12} /></button> : detail?.status === 'ready' && detail.report ? <button data-testid="button-retry-key-art" onClick={onRetry} disabled={isRetrying} className="flex h-8 flex-none items-center gap-1.5 rounded-md border border-border bg-card px-3 text-[11px] font-semibold text-primary transition hover:bg-muted disabled:opacity-60">{isRetrying ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}{isRetrying ? 'Retrying…' : 'Retry key art'}</button> : null}
      </div>
    </section>
  );
}

function Report({ report }: { report: ScreenplayDetail['report'] }) {
  if (!report) return <div data-testid="empty-coverage-report" className="rounded-xl border border-dashed border-border bg-card px-6 py-10 text-center"><div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-muted text-muted-foreground"><Clock3 size={19} /></div><h2 className="mt-4 text-base font-semibold">Coverage is in progress</h2><p className="mx-auto mt-2 max-w-sm text-xs leading-relaxed text-muted-foreground">The report will appear here once the agent has made its pass. You can keep watching the trace on the right.</p></div>;
  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-border bg-card p-5 shadow-[0_8px_24px_rgba(77,61,40,.035)] md:p-6">
        <div className="font-data text-[9px] uppercase tracking-[0.18em] text-muted-foreground">Coverage report</div>
        <div data-testid="text-logline" className="mt-3 max-w-2xl font-editorial text-[28px] leading-[1.08] text-foreground md:text-[34px]">“{report.logline}”</div>
        <div className="mt-5 grid gap-5 border-t border-border pt-5 md:grid-cols-[1fr_auto]"><div><div className="font-data text-[9px] uppercase tracking-[0.16em] text-muted-foreground">Synopsis</div><p data-testid="text-synopsis" className="mt-2 max-w-2xl text-[13px] leading-[1.7] text-muted-foreground">{report.synopsis}</p></div><div className="md:min-w-[140px] md:border-l md:border-border md:pl-5"><div className="font-data text-[9px] uppercase tracking-[0.16em] text-muted-foreground">Agent recommendation</div><div data-testid="text-recommendation" className="mt-2 text-xl font-semibold tracking-[-0.03em]">{report.recommendation}</div></div></div>
      </section>
      <div className="grid gap-4 md:grid-cols-2">
        <BulletSection title="What is working" items={report.strengths} positive />
        <BulletSection title="Pressure points" items={report.weaknesses} />
      </div>
      <section className="rounded-xl border border-border bg-card p-5 shadow-[0_8px_24px_rgba(77,61,40,.035)]"><div className="mb-4 flex items-center justify-between"><div><div className="font-data text-[9px] uppercase tracking-[0.18em] text-muted-foreground">Market context</div><h2 className="mt-1 text-[16px] font-semibold">Comparable titles</h2></div><MoreHorizontal size={16} className="text-muted-foreground" /></div><div className="divide-y divide-border">{report.comparableTitles.map((item, index) => <div data-testid={`comparable-title-${index}`} key={`${item.title}-${index}`} className="grid gap-1 py-3 first:pt-0 md:grid-cols-[145px_1fr] md:gap-5"><div className="text-[12px] font-semibold">{item.title}</div><div className="text-[12px] leading-relaxed text-muted-foreground">{item.reason}</div></div>)}</div></section>
    </div>
  );
}

function BulletSection({ title, items, positive = false }: { title: string; items: string[]; positive?: boolean }) {
  return <section className="rounded-xl border border-border bg-card p-5 shadow-[0_8px_24px_rgba(77,61,40,.035)]"><div className="mb-4 flex items-center gap-2"><div className={`flex h-6 w-6 items-center justify-center rounded-md ${positive ? 'bg-[#e8f0e8] text-[#4d7d5d]' : 'bg-[#f6ebe6] text-[#a95a49]'}`}>{positive ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}</div><h2 className="text-[15px] font-semibold">{title}</h2></div><ul className="space-y-3">{items.map((item, index) => <li data-testid={`bullet-${positive ? 'strength' : 'weakness'}-${index}`} key={`${item}-${index}`} className="flex gap-2.5 text-[12px] leading-relaxed text-muted-foreground"><span className={`mt-[7px] h-1 w-1 flex-none rounded-full ${positive ? 'bg-[#639477]' : 'bg-[#bd7665]'}`} />{item}</li>)}</ul></section>;
}

function DecisionBar({ detail, onDecision, isPending }: { detail: ScreenplayDetail; onDecision: (decision: DecisionInput['decision']) => void; isPending: boolean }) {
  const [confirm, setConfirm] = useState<DecisionInput['decision'] | null>(null);
  const decided = detail.decision;
  const isReady = detail.status === 'ready';
  if (decided) return <div data-testid="decision-complete" className={`flex flex-col gap-3 rounded-xl border px-5 py-4 sm:flex-row sm:items-center sm:justify-between ${decided === 'approved' ? 'border-[#c9dacf] bg-[#e8f0e8]' : 'border-[#e4c7c0] bg-[#f7e9e5]'}`}><div className="flex items-center gap-3"><div className={`flex h-8 w-8 items-center justify-center rounded-full ${decided === 'approved' ? 'bg-[#c5dccb] text-[#315846]' : 'bg-[#ebcfc7] text-[#9a493b]'}`}>{decided === 'approved' ? <Check size={16} /> : <X size={16} />}</div><div><div className="text-[13px] font-semibold">Coverage {decided}</div><div className="mt-0.5 text-[11px] text-muted-foreground">{detail.decisionAt ? `Recorded ${formatDate(detail.decisionAt)} at ${formatTime(detail.decisionAt)}` : 'Decision recorded'}</div></div></div><div className="font-data text-[9px] uppercase tracking-[0.15em] text-muted-foreground">Finalized</div></div>;
  return <section data-testid="decision-bar" className="rounded-xl border border-[#d8cdbd] bg-[#f3ece0] p-5"><div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between"><div><div className="flex items-center gap-2"><div className="h-1.5 w-1.5 rounded-full bg-primary" /><div className="font-data text-[9px] uppercase tracking-[0.18em] text-[#806f5d]">Your call</div></div><h2 className="mt-1.5 text-[16px] font-semibold tracking-[-0.02em]">Make the greenlight decision</h2><p className="mt-1 text-[11px] text-muted-foreground">{isReady ? 'Nothing moves forward until you explicitly decide.' : 'Decision controls unlock when coverage is ready.'}</p></div><div className="flex gap-2">{confirm ? <><button data-testid="button-cancel-decision" onClick={() => setConfirm(null)} className="h-10 rounded-md border border-border bg-card px-4 text-xs font-semibold text-muted-foreground transition hover:bg-muted">Cancel</button><button data-testid="button-confirm-decision" onClick={() => { onDecision(confirm); setConfirm(null); }} disabled={isPending} className={`flex h-10 items-center gap-2 rounded-md px-4 text-xs font-semibold text-primary-foreground transition disabled:opacity-60 ${confirm === 'approved' ? 'bg-[#3f7555] hover:bg-[#315e45]' : 'bg-[#a95545] hover:bg-[#8f4539]'}`}>{isPending && <Loader2 size={13} className="animate-spin" />} Confirm {confirm}</button></> : <><button data-testid="button-reject-screenplay" disabled={!isReady || isPending} onClick={() => setConfirm('rejected')} className="flex h-10 items-center gap-2 rounded-md border border-[#d9bdb4] bg-card px-4 text-xs font-semibold text-[#985143] transition hover:bg-[#f8e9e5] disabled:cursor-not-allowed disabled:opacity-40"><XCircle size={14} /> Reject</button><button data-testid="button-approve-screenplay" disabled={!isReady || isPending} onClick={() => setConfirm('approved')} className="flex h-10 items-center gap-2 rounded-md bg-primary px-4 text-xs font-semibold text-primary-foreground transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"><Check size={14} /> Approve coverage</button></>}</div></div></section>;
}

function UploadDialog({ open, onClose, onUploaded }: { open: boolean; onClose: () => void; onUploaded: (id: string) => void }) {
  const analyze = useAnalyzeScreenplay();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState('');
  if (!open) return null;
  const chooseFile = (next: File | undefined) => {
    setError('');
    if (!next) return;
    if (!['application/pdf', 'text/plain'].includes(next.type) && !next.name.toLowerCase().endsWith('.txt') && !next.name.toLowerCase().endsWith('.pdf')) { setError('Choose a PDF or plain-text screenplay.'); return; }
    setFile(next);
  };
  const submit = async () => {
    if (!file) return;
    try {
      const content = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result ?? ''));
        reader.onerror = () => reject(new Error('Could not read file'));
        if (file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')) reader.readAsDataURL(file);
        else reader.readAsText(file);
      });
      const job = await analyze.mutateAsync({ data: { fileName: file.name, mimeType: file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf') ? 'application/pdf' : 'text/plain', content } });
      onUploaded(job.screenplayId);
      setFile(null);
      onClose();
    } catch { setError('This file could not be queued. Please try again.'); }
  };
  return <div data-testid="dialog-upload-screenplay" className="fixed inset-0 z-20 flex items-center justify-center bg-[#192c28]/35 p-4 backdrop-blur-[2px]"><div className="w-full max-w-[500px] rounded-2xl border border-border bg-card p-6 shadow-[0_24px_70px_rgba(25,44,40,.2)] rise-in"><div className="flex items-start justify-between"><div><div className="font-data text-[9px] uppercase tracking-[0.18em] text-primary">New coverage</div><h2 className="mt-1 font-editorial text-3xl italic">Put a story on the table.</h2><p className="mt-2 text-xs leading-relaxed text-muted-foreground">Upload the draft and let the agent build a considered first pass.</p></div><button data-testid="button-close-upload" onClick={onClose} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted" aria-label="Close upload dialog"><X size={17} /></button></div><div onDragOver={(event: DragEvent<HTMLDivElement>) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event: DragEvent<HTMLDivElement>) => { event.preventDefault(); setDragging(false); chooseFile(event.dataTransfer.files?.[0]); }} onClick={() => inputRef.current?.click()} className={`mt-6 cursor-pointer rounded-xl border border-dashed p-8 text-center transition ${dragging ? 'border-primary bg-[#f8e9e3]' : 'border-[#cfc3b2] bg-muted/45 hover:border-primary hover:bg-[#f8eee9]'}`}><input data-testid="input-screenplay-file" ref={inputRef} type="file" accept=".pdf,.txt,application/pdf,text/plain" className="hidden" onChange={(event: ChangeEvent<HTMLInputElement>) => chooseFile(event.target.files?.[0])} />{file ? <><div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-[#e8f0e8] text-[#477458]"><FileText size={20} /></div><div className="mt-3 text-sm font-semibold">{file.name}</div><div className="mt-1 text-[11px] text-muted-foreground">{(file.size / 1024 / 1024).toFixed(2)} MB · {file.type === 'application/pdf' ? 'PDF' : 'Plain text'}</div></> : <><div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-card text-primary shadow-sm"><Upload size={19} /></div><div className="mt-3 text-sm font-semibold">Drop a screenplay here</div><div className="mt-1 text-[11px] text-muted-foreground">or click to browse · PDF or TXT</div></>}</div>{error && <div data-testid="text-upload-error" className="mt-3 flex items-center gap-2 text-xs text-destructive"><AlertCircle size={14} />{error}</div>}<div className="mt-6 flex justify-end gap-2"><button data-testid="button-cancel-upload" onClick={onClose} className="h-10 rounded-md border border-border px-4 text-xs font-semibold text-muted-foreground hover:bg-muted">Cancel</button><button data-testid="button-start-analysis" disabled={!file || analyze.isPending} onClick={submit} className="flex h-10 items-center gap-2 rounded-md bg-primary px-4 text-xs font-semibold text-primary-foreground transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-45">{analyze.isPending && <Loader2 size={14} className="animate-spin" />}{analyze.isPending ? 'Queueing…' : 'Start coverage'}</button></div></div></div>;
}

function EmptyWorkspace({ onUpload }: { onUpload: () => void }) {
  return <div data-testid="empty-workspace" className="flex min-h-[calc(100dvh-77px)] flex-col items-center justify-center px-6 py-16 text-center"><div className="relative flex h-20 w-20 items-center justify-center rounded-[22px] border border-[#d8cdbd] bg-[#f3ece0] text-primary"><Film size={31} strokeWidth={1.3} /><div className="absolute -right-2 -top-2 flex h-7 w-7 items-center justify-center rounded-full bg-primary text-primary-foreground"><Plus size={14} /></div></div><div className="mt-7 font-data text-[9px] uppercase tracking-[0.2em] text-primary">The desk is yours</div><h2 className="mt-2 font-editorial text-4xl italic tracking-[-0.02em]">Start with a screenplay.</h2><p className="mt-3 max-w-[360px] text-[13px] leading-relaxed text-muted-foreground">Upload a PDF or plain-text draft. Greenlight Desk will return a traceable read, a visual north star, and a decision point.</p><button data-testid="button-empty-upload" onClick={onUpload} className="mt-7 flex h-11 items-center gap-2 rounded-md bg-primary px-5 text-xs font-semibold text-primary-foreground shadow-[0_7px_18px_rgba(164,75,48,.15)] transition hover:brightness-110 active:scale-[.98]"><Upload size={15} /> Upload first screenplay</button><div className="mt-10 flex items-center gap-5 text-[10px] text-muted-foreground"><span className="flex items-center gap-1.5"><FileText size={12} /> PDF / TXT</span><span className="h-3 w-px bg-border" /><span className="flex items-center gap-1.5"><ShieldCheck size={12} /> Private workspace</span></div></div>;
}

function LoadingWorkspace() {
  return <div data-testid="loading-workspace" className="space-y-5 p-5 md:p-8"><div className="shimmer h-7 w-2/3 rounded-md" /><div className="shimmer h-36 rounded-xl" /><div className="grid gap-4 md:grid-cols-2"><div className="shimmer h-44 rounded-xl" /><div className="shimmer h-44 rounded-xl" /></div></div>;
}

function DetailWorkspace({ detail, onDecision, isDeciding, onRestart, isRestarting, restartError, onRetryKeyArt, isRetryingKeyArt, keyArtRetryError }: { detail: ScreenplayDetail; onDecision: (decision: DecisionInput['decision']) => void; isDeciding: boolean; onRestart: () => void; isRestarting: boolean; restartError: boolean; onRetryKeyArt: () => void; isRetryingKeyArt: boolean; keyArtRetryError: boolean }) {
  return <div className="page-in grid gap-5 p-5 md:p-8 xl:grid-cols-[minmax(0,1fr)_310px]"><main className="min-w-0 space-y-4"><div className="flex items-center justify-between"><div className="flex items-center gap-2 text-[11px] text-muted-foreground"><FolderOpen size={13} /> {detail.pageCount ? `${detail.pageCount} pages` : 'Page count pending'} <span className="text-border">·</span> Added {formatDate(detail.createdAt)}</div>{detail.mimeType === 'application/pdf' && <span className="font-data text-[9px] uppercase tracking-[0.16em] text-muted-foreground">PDF source</span>}</div>{detail.status === 'failed' && <section data-testid="failed-analysis-panel" className="flex flex-col gap-4 rounded-xl border border-[#e4c7c0] bg-[#f7e9e5] p-5 sm:flex-row sm:items-center sm:justify-between"><div><div className="flex items-center gap-2 text-[13px] font-semibold text-[#8f4539]"><AlertCircle size={16} /> Analysis needs another pass</div><p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">Restart with the saved screenplay. The previous failed trace will be replaced with a fresh run.</p>{restartError && <p data-testid="text-restart-error" className="mt-2 text-[11px] font-medium text-destructive">The restart could not be queued. Please try again.</p>}</div><button data-testid="button-restart-analysis" onClick={onRestart} disabled={isRestarting} className="flex h-10 flex-none items-center justify-center gap-2 rounded-md bg-primary px-4 text-xs font-semibold text-primary-foreground transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60">{isRestarting ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}{isRestarting ? 'Restarting…' : 'Restart analysis'}</button></section>}<Report report={detail.report} /><DecisionBar detail={detail} onDecision={onDecision} isPending={isDeciding} /></main><aside className="space-y-4"><Trace trace={detail.trace} status={detail.status} /><KeyArt detail={detail} onRetry={onRetryKeyArt} isRetrying={isRetryingKeyArt} retryError={keyArtRetryError} /></aside></div>;
}

function Workspace() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string>();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [mobileRailOpen, setMobileRailOpen] = useState(false);
  const listQuery = useListScreenplays({ query: { queryKey: getListScreenplaysQueryKey(), staleTime: 15000 } });
  const screenplays = listQuery.data ?? [];
  useEffect(() => { if (!selectedId && screenplays[0]?.id) setSelectedId(screenplays[0].id); }, [screenplays, selectedId]);
  const detailQuery = useGetScreenplay(selectedId ?? '', { query: { enabled: Boolean(selectedId), queryKey: getGetScreenplayQueryKey(selectedId ?? ''), refetchInterval: (query) => { const current = query.state.data as ScreenplayDetail | undefined; return current?.status === 'queued' || current?.status === 'analyzing' ? 3500 : false; } } });
  const decide = useDecideScreenplay();
  const restart = useRestartScreenplay();
  const retryKeyArt = useRetryScreenplayKeyArt();
  const detail = detailQuery.data;
  const onUploaded = (id: string) => { setSelectedId(id); void queryClient.invalidateQueries({ queryKey: getListScreenplaysQueryKey() }); };
  const onDecision = (decision: DecisionInput['decision']) => {
    if (!selectedId) return;
    decide.mutate({ screenplayId: selectedId, data: { decision } }, { onSuccess: (result) => {
      queryClient.setQueryData(getGetScreenplayQueryKey(selectedId), result);
      queryClient.setQueryData<ScreenplaySummary[]>(getListScreenplaysQueryKey(), (old) => old?.map((item) => item.id === selectedId ? { ...item, status: decision } : item));
    } });
  };
  const onRestart = async () => {
    if (!selectedId) return;
    try {
      await restart.mutateAsync({ screenplayId: selectedId });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: getGetScreenplayQueryKey(selectedId) }),
        queryClient.invalidateQueries({ queryKey: getListScreenplaysQueryKey() }),
      ]);
    } catch {
      // The mutation error state renders an inline retry message.
    }
  };
  const onRetryKeyArt = () => {
    if (!selectedId) return;
    retryKeyArt.mutate(
      { screenplayId: selectedId },
      { onSuccess: (result) => queryClient.setQueryData(getGetScreenplayQueryKey(selectedId), result) },
    );
  };
  return <div className="grain flex min-h-[100dvh] flex-col bg-background md:flex-row">
    <div className={`${mobileRailOpen ? 'block' : 'hidden'} fixed inset-0 z-10 bg-sidebar/30 md:hidden`} onClick={() => setMobileRailOpen(false)} />
    <div className="hidden md:flex"><Rail screenplays={screenplays} selectedId={selectedId} onSelect={setSelectedId} onUpload={() => setUploadOpen(true)} onRefresh={() => void listQuery.refetch()} isRefreshing={listQuery.isFetching} /></div>
    {mobileRailOpen && <div className="fixed inset-y-0 left-0 z-20 flex w-[286px] shadow-[12px_0_30px_rgba(25,44,40,.18)] md:hidden"><Rail screenplays={screenplays} selectedId={selectedId} onSelect={(id) => { setSelectedId(id); setMobileRailOpen(false); }} onUpload={() => { setMobileRailOpen(false); setUploadOpen(true); }} onRefresh={() => void listQuery.refetch()} isRefreshing={listQuery.isFetching} /></div>}
    <div className="flex min-w-0 flex-1 flex-col"><WorkspaceHeader detail={detail} onMenu={() => setMobileRailOpen(true)} /><div className="flex-1 overflow-y-auto">{listQuery.isLoading ? <LoadingWorkspace /> : listQuery.isError ? <div data-testid="error-screenplays" className="flex min-h-[60vh] flex-col items-center justify-center px-6 text-center"><div className="flex h-11 w-11 items-center justify-center rounded-full bg-[#f7e9e5] text-destructive"><AlertCircle size={20} /></div><h2 className="mt-4 font-semibold">Could not load the desk</h2><p className="mt-1 text-xs text-muted-foreground">The screenplay list is taking a moment.</p><button data-testid="button-retry-screenplays" onClick={() => void listQuery.refetch()} className="mt-5 flex h-9 items-center gap-2 rounded-md border border-border bg-card px-4 text-xs font-semibold hover:bg-muted"><RefreshCw size={13} /> Try again</button></div> : !screenplays.length ? <EmptyWorkspace onUpload={() => setUploadOpen(true)} /> : detailQuery.isLoading || !detail ? <LoadingWorkspace /> : detailQuery.isError ? <div data-testid="error-detail" className="flex min-h-[60vh] flex-col items-center justify-center px-6 text-center"><AlertCircle size={22} className="text-destructive" /><h2 className="mt-4 font-semibold">This read is unavailable</h2><button data-testid="button-retry-detail" onClick={() => void detailQuery.refetch()} className="mt-4 flex h-9 items-center gap-2 rounded-md border border-border bg-card px-4 text-xs font-semibold hover:bg-muted"><RefreshCw size={13} /> Reload coverage</button></div> : <DetailWorkspace detail={detail} onDecision={onDecision} isDeciding={decide.isPending} onRestart={() => void onRestart()} isRestarting={restart.isPending} restartError={restart.isError} onRetryKeyArt={onRetryKeyArt} isRetryingKeyArt={retryKeyArt.isPending} keyArtRetryError={retryKeyArt.isError} />}</div></div>
    <UploadDialog open={uploadOpen} onClose={() => setUploadOpen(false)} onUploaded={onUploaded} />
  </div>;
}

function Router() {
  return <RoutedErrorBoundary><Switch><Route path="/" component={Workspace} /><Route component={NotFound} /></Switch></RoutedErrorBoundary>;
}

function RoutedErrorBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>;
}

function App() {
  return <QueryClientProvider client={queryClient}><TooltipProvider><WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}><Router /></WouterRouter><Toaster /></TooltipProvider></QueryClientProvider>;
}

export default App;