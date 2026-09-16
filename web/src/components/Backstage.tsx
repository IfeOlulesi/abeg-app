import { useEffect, useState } from 'react';
import type { ComponentType, ReactNode, SVGProps } from 'react';
import {
  BackstageIcon,
  CloseIcon,
  TrophyIcon,
  ResetIcon,
  DotIcon,
  SearchIcon,
  SlidersIcon,
  BrainIcon,
  SparkIcon,
  ShieldIcon,
  TargetIcon,
  ChipIcon,
  ClockIcon,
  CoinIcon,
  LayersIcon,
  FlaskIcon,
  BoxIcon,
  BotIcon,
} from './Icons';
import { naira, firstLine, formatCost, formatMs, tempLabel } from '../lib/format';
import ByokCard from './ByokCard';
import type {
  EvalCaseResult,
  EvalReport,
  EvalStatus,
  Model,
  Product,
  Script,
  SystemPromptBody,
  TimelineItem,
  Trace,
  TraceStep,
} from '../types';

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

/* ------------------------------------------------------------------ */
/* small shared bits                                                   */
/* ------------------------------------------------------------------ */
function Toggle({ on, onChange }: { on: boolean; onChange: (next: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
      className={`relative inline-flex h-6 w-11 flex-none items-center rounded-full transition ${
        on ? 'bg-green-500' : 'bg-stone-200 dark:bg-stone-700'
      }`}
    >
      <span className={`h-5 w-5 rounded-full bg-white shadow transition-all ${on ? 'ml-5' : 'ml-0.5'}`} />
    </button>
  );
}

// A labelled lesson card: icon + title + one-line plain-language "what this is".
interface KnobProps {
  icon: IconType;
  title: string;
  blurb: string;
  children: ReactNode;
  accent?: 'stone' | 'coral' | 'green';
}
function Knob({ icon: Icon, title, blurb, children, accent = 'stone' }: KnobProps) {
  const tone =
    accent === 'coral'
      ? 'bg-[#FEF0EB] text-[#F0532B]'
      : accent === 'green'
        ? 'bg-green-50 text-green-600 dark:bg-green-500/15 dark:text-green-400'
        : 'bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400';
  return (
    <section className="rounded-2xl bg-white p-4 ring-1 ring-stone-100 dark:bg-stone-900 dark:ring-stone-800">
      <div className="flex items-start gap-3">
        <div className={`grid h-9 w-9 flex-none place-items-center rounded-xl ${tone}`}>
          <Icon className="h-[18px] w-[18px]" />
        </div>
        <div className="min-w-0">
          <div className="text-[15px] font-bold text-stone-900 dark:text-stone-100">{title}</div>
          <p className="mt-0.5 text-[12.5px] leading-snug text-stone-500 dark:text-stone-400">{blurb}</p>
        </div>
      </div>
      <div className="mt-3.5">{children}</div>
    </section>
  );
}

function TryButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg bg-[#FEF0EB] px-3 py-1.5 text-[12.5px] font-bold text-[#F0532B] transition hover:bg-[#fde2d8]"
    >
      <FlaskIcon className="h-3.5 w-3.5" />
      {children}
    </button>
  );
}

const TABS: { id: string; label: string; icon: IconType }[] = [
  { id: 'tinker', label: 'Tinker', icon: SlidersIcon },
  { id: 'anatomy', label: 'X-ray', icon: SearchIcon },
  { id: 'report', label: 'Report Card', icon: ShieldIcon },
  { id: 'stock', label: 'Stock', icon: BoxIcon },
];

/* ------------------------------------------------------------------ */
/* main                                                                */
/* ------------------------------------------------------------------ */
interface BackstageProps {
  open: boolean;
  onClose: () => void;
  guardrails: boolean;
  cached: boolean;
  onTask: boolean;
  onToggleGuardrails: (next: boolean) => void;
  onToggleCached: (next: boolean) => void;
  onToggleOnTask: (next: boolean) => void;
  temperature: number;
  onTemperature: (value: number) => void;
  model: string;
  models: Model[];
  onModel: (m: string) => void;
  systemPrompt: string;
  defaultPrompt: string;
  promptCustomized: boolean;
  onSystemPrompt: (body: SystemPromptBody) => void;
  onRace: () => void;
  onReset: () => void;
  onScript: (n: number) => void;
  onAsk: (text: string) => void;
  scripts: Script[];
  products: Product[];
  timeline: TimelineItem[];
  trace: Trace | null;
  report: EvalReport | null;
  onRunEvals: () => void;
  evalsRunning: boolean;
  showReportSignal: number;
}

export default function Backstage({
  open,
  onClose,
  guardrails,
  cached,
  onTask,
  onToggleGuardrails,
  onToggleCached,
  onToggleOnTask,
  temperature,
  onTemperature,
  model,
  models,
  onModel,
  systemPrompt,
  defaultPrompt,
  promptCustomized,
  onSystemPrompt,
  onRace,
  onReset,
  onScript,
  onAsk,
  scripts,
  products,
  timeline,
  trace,
  report,
  onRunEvals,
  evalsRunning,
  showReportSignal,
}: BackstageProps) {
  const [tab, setTab] = useState('tinker');
  const [draft, setDraft] = useState(systemPrompt || '');

  // Keep the editor in sync when the prompt changes elsewhere (e.g. reset).
  useEffect(() => {
    setDraft(systemPrompt || '');
  }, [systemPrompt]);

  // Pressing the E shortcut bumps this signal; jump to the Report Card tab.
  useEffect(() => {
    if (showReportSignal > 0) setTab('report');
  }, [showReportSignal]);

  const promptDirty = draft.trim() !== (systemPrompt || '').trim();
  const scriptList =
    scripts && scripts.length ? scripts : [1, 2, 3, 4].map((n) => ({ n, message: `Script ${n}` }));

  return (
    <aside
      className={`fixed inset-y-0 right-0 z-50 flex w-[440px] flex-col border-l border-stone-200 bg-stone-50 transition-transform duration-300 ease-out dark:border-stone-800 dark:bg-stone-950 ${
        open ? 'translate-x-0' : 'translate-x-full'
      }`}
    >
      {/* header — matches the app header height (h-16) */}
      <div className="flex h-16 shrink-0 items-center gap-3 border-b border-stone-200 bg-white px-5 dark:border-stone-800 dark:bg-stone-900">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-stone-900 text-white dark:bg-stone-700">
          <BackstageIcon className="h-4 w-4" />
        </div>
        <div className="leading-tight">
          <div className="font-bold text-stone-900 dark:text-stone-100">The Workshop</div>
          <div className="text-xs text-stone-400 dark:text-stone-500">Look inside the AI — and turn the knobs</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto grid h-8 w-8 place-items-center rounded-full text-stone-400 transition hover:bg-stone-100 hover:text-stone-700 dark:text-stone-500 dark:hover:bg-stone-800 dark:hover:text-stone-200"
        >
          <CloseIcon className="h-5 w-5" />
        </button>
      </div>

      {/* pinned: fire a test message from anywhere */}
      <div className="border-b border-stone-200 bg-white px-5 py-3 dark:border-stone-800 dark:bg-stone-900">
        <div className="mb-2 text-[11px] font-bold uppercase tracking-wide text-stone-400 dark:text-stone-500">
          Send a test message
        </div>
        <div className="flex flex-wrap gap-1.5">
          {scriptList.map((s) => (
            <button
              key={s.n}
              type="button"
              onClick={() => onScript(s.n)}
              title={s.message}
              className="max-w-[190px] truncate rounded-lg bg-stone-100 px-2.5 py-1.5 text-[12px] font-medium text-stone-600 transition hover:bg-stone-200 dark:bg-stone-800 dark:text-stone-300 dark:hover:bg-stone-700"
            >
              <span className="mr-1 font-bold text-stone-400 dark:text-stone-500">{s.n}</span>
              {s.message}
            </button>
          ))}
        </div>
      </div>

      {/* tabs */}
      <div className="flex gap-1 border-b border-stone-200 bg-white px-3 dark:border-stone-800 dark:bg-stone-900">
        {TABS.map((t) => {
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`relative flex items-center gap-1.5 px-3 py-2.5 text-[13px] font-bold transition ${
                active ? 'text-[#F0532B]' : 'text-stone-400 hover:text-stone-600 dark:text-stone-500 dark:hover:text-stone-300'
              }`}
            >
              <t.icon className="h-4 w-4" />
              {t.label}
              {active && (
                <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-[#F0532B]" />
              )}
            </button>
          );
        })}
      </div>

      <div className="grow overflow-y-auto p-4">
        {tab === 'tinker' && (
          <div className="space-y-3.5">
            {/* system prompt */}
            <Knob
              icon={BrainIcon}
              accent="coral"
              title="The AI's instructions"
              blurb="The exact words we hand the AI before every chat. This is the whole rulebook — no magic. Edit it and watch it obey."
            >
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={7}
                spellCheck={false}
                className="w-full resize-none rounded-xl border border-stone-200 bg-stone-50 p-3 text-[12.5px] leading-relaxed text-stone-700 outline-none focus:border-[#F0532B] focus:bg-white dark:border-stone-700 dark:bg-stone-800 dark:text-stone-300 dark:focus:bg-stone-800"
              />
              <div className="mt-2 flex items-center gap-2">
                <button
                  type="button"
                  disabled={!promptDirty}
                  onClick={() => onSystemPrompt({ prompt: draft })}
                  className={`rounded-lg px-3 py-1.5 text-[12.5px] font-bold transition ${
                    promptDirty
                      ? 'bg-stone-900 text-white hover:bg-stone-800 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-white'
                      : 'cursor-not-allowed bg-stone-100 text-stone-300 dark:bg-stone-800 dark:text-stone-600'
                  }`}
                >
                  Save instructions
                </button>
                <button
                  type="button"
                  disabled={!promptCustomized && !promptDirty}
                  onClick={() => {
                    setDraft(defaultPrompt || '');
                    onSystemPrompt({ reset: true });
                  }}
                  className="rounded-lg px-3 py-1.5 text-[12.5px] font-semibold text-stone-500 transition enabled:hover:bg-stone-100 disabled:cursor-not-allowed disabled:text-stone-300 dark:text-stone-400 dark:enabled:hover:bg-stone-800 dark:disabled:text-stone-600"
                >
                  Reset to original
                </button>
                {promptCustomized && (
                  <span className="ml-auto rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-bold text-amber-600 dark:bg-amber-500/15 dark:text-amber-300">
                    edited
                  </span>
                )}
              </div>
              <p className="mt-2 text-[11.5px] text-stone-400 dark:text-stone-500">
                Try: add “Always answer like a cheerful pirate.” then send a test message.
              </p>
            </Knob>

            {/* temperature */}
            <Knob
              icon={SparkIcon}
              title="Creativity dial"
              blurb="How adventurous the AI is. Low = focused and repeatable. High = surprising (and riskier)."
            >
              <div className="flex items-center gap-3">
                <span className="text-[11px] font-semibold text-stone-400 dark:text-stone-500">Focused</span>
                <input
                  type="range"
                  min={0}
                  max={1.5}
                  step={0.1}
                  value={temperature ?? 0.3}
                  onChange={(e) => onTemperature(parseFloat(e.target.value))}
                  className="h-1.5 grow cursor-pointer appearance-none rounded-full bg-stone-200 accent-[#F0532B] dark:bg-stone-700"
                />
                <span className="text-[11px] font-semibold text-stone-400 dark:text-stone-500">Wild</span>
              </div>
              <div className="mt-2 flex items-center justify-between">
                <span className="rounded-full bg-stone-100 px-2.5 py-0.5 text-[12px] font-bold text-stone-600 dark:bg-stone-800 dark:text-stone-300">
                  {Number(temperature ?? 0.3).toFixed(1)} · {tempLabel(temperature ?? 0.3)}
                </span>
                <TryButton onClick={() => onAsk('What would you recommend I eat today?')}>
                  Ask for a suggestion
                </TryButton>
              </div>
              <p className="mt-2 text-[11.5px] text-stone-400 dark:text-stone-500">
                Tap it a few times at a low setting (near-identical replies), then crank it up and tap
                again — watch the suggestions get wilder.
              </p>
            </Knob>

            {/* grounding */}
            <Knob
              icon={ShieldIcon}
              accent={guardrails ? 'green' : 'stone'}
              title="Grounding (the guardrail)"
              blurb="On: the AI answers only from OUR live database — and refuses to speculate. Off: it happily answers from its own memory, which can be wrong or made-up."
            >
              <div className="flex items-center justify-between">
                <span
                  className={`text-[13px] font-bold ${guardrails ? 'text-green-600 dark:text-green-400' : 'text-stone-400 dark:text-stone-500'}`}
                >
                  {guardrails ? 'On — only trusts the database' : 'Off — free to answer from memory'}
                </span>
                <Toggle on={guardrails} onChange={onToggleGuardrails} />
              </div>
              <div className="mt-3 flex items-center justify-between gap-2 rounded-xl bg-stone-50 px-3 py-2 dark:bg-stone-800">
                <span className="min-w-0 text-[11.5px] text-stone-500 dark:text-stone-400">
                  Ask a general-knowledge question and watch it guess (off) vs. check the menu (on).
                </span>
                <TryButton
                  onClick={() =>
                    onAsk('Roughly how much does a plate of jollof usually cost at a party in Lagos?')
                  }
                >
                  Try it
                </TryButton>
              </div>
            </Knob>

            {/* stay on task — prompt-injection defense */}
            <Knob
              icon={TargetIcon}
              accent={onTask ? 'green' : 'stone'}
              title="Stay on task"
              blurb="The famous one: a customer once talked a fast-food bot into writing code instead of taking the order. On: the AI politely refuses anything that isn't food. Off: it can be hijacked."
            >
              <div className="flex items-center justify-between">
                <span
                  className={`text-[13px] font-bold ${onTask ? 'text-green-600 dark:text-green-400' : 'text-stone-400 dark:text-stone-500'}`}
                >
                  {onTask ? 'On — sticks to food orders' : 'Off — can be hijacked'}
                </span>
                <Toggle on={onTask} onChange={onToggleOnTask} />
              </div>
              <div className="mt-3 flex items-center justify-between gap-2 rounded-xl bg-stone-50 px-3 py-2 dark:bg-stone-800">
                <span className="min-w-0 text-[11.5px] text-stone-500 dark:text-stone-400">
                  Order jollof, then ask it to write Python for you — off, it obliges; on, it declines.
                </span>
                <TryButton onClick={() => onScript(4)}>Try it</TryButton>
              </div>
            </Knob>

            {/* model */}
            <Knob
              icon={ChipIcon}
              title="The brain (model)"
              blurb="Swap the underlying AI. Bigger brains are often smarter, but slower and pricier — you'll see it in the X-ray."
            >
              <select
                value={model || ''}
                onChange={(e) => onModel(e.target.value)}
                className="w-full rounded-xl border border-stone-200 bg-white px-3 py-2.5 text-[13px] font-semibold text-stone-700 outline-none focus:border-[#F0532B] dark:border-stone-700 dark:bg-stone-900 dark:text-stone-300"
              >
                {(models || []).map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label} — {m.note}
                  </option>
                ))}
              </select>
            </Knob>

            {/* cached mode (demo safety) */}
            <section className="flex items-center gap-3 rounded-2xl bg-white px-4 py-3 ring-1 ring-stone-100 dark:bg-stone-900 dark:ring-stone-800">
              <div className="min-w-0">
                <div className="text-[13.5px] font-bold text-stone-800 dark:text-stone-200">Cached mode</div>
                <p className="text-[11.5px] leading-snug text-stone-400 dark:text-stone-500">
                  Plays pre-recorded answers with no AI calls — a safety net if the wifi dies (also free).
                </p>
              </div>
              <Toggle on={cached} onChange={onToggleCached} />
            </section>

            {/* bring your own key */}
            <ByokCard />
          </div>
        )}

        {tab === 'anatomy' && <Anatomy trace={trace} timeline={timeline} />}

        {tab === 'report' && (
          <ReportCard report={report} onRun={onRunEvals} running={evalsRunning} />
        )}

        {tab === 'stock' && (
          <div className="space-y-4">
            <Knob
              icon={TrophyIcon}
              title="What breaks under load"
              blurb="Two customers grab the last plate at the exact same instant. With grounding off the shop oversells (stock goes below zero). With it on, one wins and stock never goes negative."
            >
              <div className="grid grid-cols-2 gap-2.5">
                <button
                  type="button"
                  onClick={onRace}
                  className="flex items-center justify-center gap-2 rounded-xl bg-stone-900 px-3 py-2.5 text-[13px] font-bold text-white transition hover:bg-stone-800 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-white"
                >
                  <TrophyIcon className="h-4 w-4" />
                  Run the race
                </button>
                <button
                  type="button"
                  onClick={onReset}
                  className="flex items-center justify-center gap-2 rounded-xl bg-stone-100 px-3 py-2.5 text-[13px] font-bold text-stone-600 transition hover:bg-stone-200 dark:bg-stone-800 dark:text-stone-300 dark:hover:bg-stone-700"
                >
                  <ResetIcon className="h-4 w-4" />
                  Reset
                </button>
              </div>
            </Knob>

            <section>
              <div className="mb-2 px-1 text-[13px] font-bold text-stone-800 dark:text-stone-200">Live inventory</div>
              <div className="rounded-2xl bg-white p-2 ring-1 ring-stone-100 dark:bg-stone-900 dark:ring-stone-800">
                {products.map((p) => {
                  const avail = Number(p.available);
                  const oversold = avail < 0;
                  return (
                    <div
                      key={p.sku}
                      className={`flex items-center rounded-xl px-3 py-2 text-[14px] ${
                        oversold ? 'bg-rose-50 dark:bg-rose-500/15' : ''
                      }`}
                    >
                      <span className={`w-7 ${oversold ? 'text-rose-400 dark:text-rose-400' : 'text-stone-300 dark:text-stone-600'}`}>
                        <DotIcon className="h-3.5 w-3.5" />
                      </span>
                      <span className="flex-1 text-stone-700 dark:text-stone-300">{p.name}</span>
                      {oversold ? (
                        <span className="tnum font-bold text-rose-500 dark:text-rose-400">{avail} · oversold</span>
                      ) : (
                        <span className="tnum text-stone-500 dark:text-stone-400">{avail} left</span>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          </div>
        )}
      </div>
    </aside>
  );
}

/* ------------------------------------------------------------------ */
/* X-ray tab: anatomy of the last answer                               */
/* ------------------------------------------------------------------ */
function MetricChip({ icon: Icon, value, label }: { icon: IconType; value: ReactNode; label: string }) {
  return (
    <div className="rounded-xl bg-white p-3 ring-1 ring-stone-100 dark:bg-stone-900 dark:ring-stone-800">
      <div className="flex items-center gap-1.5 text-stone-400 dark:text-stone-500">
        <Icon className="h-3.5 w-3.5" />
        <span className="text-[10.5px] font-bold uppercase tracking-wide">{label}</span>
      </div>
      <div className="mt-1 text-[16px] font-bold tnum text-stone-900 dark:text-stone-100">{value}</div>
    </div>
  );
}

const STEP_ICON: Record<TraceStep['kind'], IconType> = {
  user: DotIcon,
  decide: SparkIcon,
  tool: SearchIcon,
  reply: BotIcon,
};

function StepRow({ step }: { step: TraceStep }) {
  const Icon = STEP_ICON[step.kind] || DotIcon;
  let title = '';
  let body: ReactNode = null;
  let tint = 'bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400';
  if (step.kind === 'user') {
    title = 'You said';
    body = <span className="italic text-stone-500 dark:text-stone-400">“{step.text}”</span>;
    tint = 'bg-[#FEF0EB] text-[#F0532B]';
  } else if (step.kind === 'decide') {
    title = `The AI decided to use ${step.count} tool${step.count === 1 ? '' : 's'}`;
    tint = 'bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300';
  } else if (step.kind === 'tool') {
    title = step.title || 'Used a tool';
    body = (
      <span className="text-stone-500 dark:text-stone-400">
        {step.outcome}
        {step.ms != null && <span className="text-stone-300 dark:text-stone-600"> · {step.ms} ms</span>}
      </span>
    );
    tint = 'bg-sky-50 text-sky-600 dark:bg-sky-500/15 dark:text-sky-400';
  } else if (step.kind === 'reply') {
    title = 'The AI replied';
    body = <span className="text-stone-500 dark:text-stone-400">{firstLine(step.text)}</span>;
    tint = 'bg-green-50 text-green-600 dark:bg-green-500/15 dark:text-green-400';
  }
  return (
    <li className="relative flex gap-3">
      <span className={`grid h-7 w-7 flex-none place-items-center rounded-full ${tint}`}>
        <Icon className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0 pb-1 pt-0.5">
        <div className="text-[13px] font-bold text-stone-800 dark:text-stone-200">{title}</div>
        {body && <div className="mt-0.5 text-[12.5px] leading-snug">{body}</div>}
      </div>
    </li>
  );
}

function Anatomy({ trace, timeline }: { trace: Trace | null; timeline: TimelineItem[] }) {
  if (!trace) {
    return (
      <div className="grid place-items-center rounded-2xl bg-white px-6 py-14 text-center ring-1 ring-stone-100 dark:bg-stone-900 dark:ring-stone-800">
        <SearchIcon className="h-7 w-7 text-stone-300 dark:text-stone-600" />
        <p className="mt-3 text-[13px] font-semibold text-stone-500 dark:text-stone-400">Nothing to X-ray yet</p>
        <p className="mt-1 text-[12px] text-stone-400 dark:text-stone-500">
          Send a test message above and this shows exactly what happened inside the AI.
        </p>
      </div>
    );
  }

  const tk = trace.tokens;
  const modelShort = (trace.model || '').split('/').pop() || '—';

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2.5">
        <MetricChip
          icon={LayersIcon}
          label="Tokens"
          value={tk ? tk.total_tokens.toLocaleString() : trace.cached ? 'free' : '—'}
        />
        <MetricChip icon={ClockIcon} label="Time" value={formatMs(trace.total_ms)} />
        <MetricChip
          icon={CoinIcon}
          label="Cost"
          value={trace.cached ? 'free' : formatCost(trace.cost_usd)}
        />
        <MetricChip icon={ChipIcon} label="Brain" value={modelShort} />
      </div>

      <div className="flex flex-wrap gap-1.5 text-[11px] font-bold">
        {trace.ttft_ms != null && (
          <span className="rounded-full bg-white px-2.5 py-1 text-stone-500 ring-1 ring-stone-100 dark:bg-stone-900 dark:text-stone-400 dark:ring-stone-800">
            first word in {formatMs(trace.ttft_ms)}
          </span>
        )}
        {tk && (
          <span className="rounded-full bg-white px-2.5 py-1 text-stone-500 ring-1 ring-stone-100 dark:bg-stone-900 dark:text-stone-400 dark:ring-stone-800">
            {tk.prompt_tokens.toLocaleString()} in · {tk.completion_tokens.toLocaleString()} out
          </span>
        )}
        {trace.grounding_blocked && (
          <span className="rounded-full bg-green-50 px-2.5 py-1 text-green-600 dark:bg-green-500/15 dark:text-green-400">
            🛡 blocked a made-up answer
          </span>
        )}
        {trace.on_task_blocked && (
          <span className="rounded-full bg-green-50 px-2.5 py-1 text-green-600 dark:bg-green-500/15 dark:text-green-400">
            🛡 blocked an off-task request
          </span>
        )}
        {trace.bounded_hit && (
          <span className="rounded-full bg-amber-50 px-2.5 py-1 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300">
            hit the tool-call limit
          </span>
        )}
      </div>

      <section className="rounded-2xl bg-white p-4 ring-1 ring-stone-100 dark:bg-stone-900 dark:ring-stone-800">
        <div className="mb-3 text-[13px] font-bold text-stone-800 dark:text-stone-200">Step by step</div>
        <ol className="space-y-2.5">
          {(trace.steps || []).map((s, i) => (
            <StepRow key={i} step={s} />
          ))}
        </ol>
      </section>

      {timeline && timeline.length > 0 && (
        <section>
          <div className="mb-2 px-1 text-[12px] font-bold uppercase tracking-wide text-stone-400 dark:text-stone-500">
            Recent activity
          </div>
          <div className="space-y-1.5">
            {timeline.slice(0, 6).map((item) => (
              <div
                key={item.id}
                className="flex items-baseline gap-2 rounded-lg bg-white px-3 py-1.5 text-[12.5px] ring-1 ring-stone-100 dark:bg-stone-900 dark:ring-stone-800"
              >
                <span
                  className={`h-1.5 w-1.5 flex-none translate-y-1 rounded-full ${
                    item.tone === 'order'
                      ? 'bg-green-500'
                      : item.tone === 'refused'
                        ? 'bg-rose-500'
                        : 'bg-stone-300 dark:bg-stone-600'
                  }`}
                />
                <span className="font-semibold text-stone-700 dark:text-stone-300">{item.title}</span>
                {item.meta && <span className="truncate text-stone-400 dark:text-stone-500">{item.meta}</span>}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Report Card tab: deterministic guardrail eval suite                 */
/* ------------------------------------------------------------------ */
const VERDICT: Record<EvalStatus, { label: string; cls: string }> = {
  held: { label: 'Held', cls: 'bg-green-50 text-green-600 dark:bg-green-500/15 dark:text-green-400' },
  grounded: { label: 'Grounded', cls: 'bg-green-50 text-green-600 dark:bg-green-500/15 dark:text-green-400' },
  info: { label: 'OK', cls: 'bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400' },
  slipped: { label: 'Slipped', cls: 'bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300' },
  failed: { label: 'Failed', cls: 'bg-rose-50 text-rose-500 dark:bg-rose-500/15 dark:text-rose-400' },
};

function RunEvalsButton({
  onRun,
  running,
  subtle,
}: {
  onRun: () => void;
  running: boolean;
  subtle?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onRun}
      disabled={running}
      className={
        subtle
          ? 'inline-flex flex-none items-center gap-2 rounded-lg bg-stone-100 px-3 py-1.5 text-[12.5px] font-bold text-stone-600 transition enabled:hover:bg-stone-200 disabled:opacity-60 dark:bg-stone-800 dark:text-stone-300 dark:enabled:hover:bg-stone-700'
          : 'inline-flex items-center gap-2 rounded-xl bg-stone-900 px-4 py-2.5 text-[13px] font-bold text-white transition enabled:hover:bg-stone-800 disabled:opacity-60 dark:bg-stone-100 dark:text-stone-900 dark:enabled:hover:bg-white'
      }
    >
      <ShieldIcon className="h-4 w-4" />
      {running ? 'Running...' : subtle ? 'Run again' : 'Run the Report Card'}
    </button>
  );
}

function CaseRow({ c }: { c: EvalCaseResult }) {
  const v = VERDICT[c.status] || VERDICT.info;
  return (
    <li className="rounded-2xl bg-white p-3.5 ring-1 ring-stone-100 dark:bg-stone-900 dark:ring-stone-800">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-bold text-stone-800 dark:text-stone-200">{c.title}</span>
        <span className={`flex-none rounded-full px-2.5 py-0.5 text-[11px] font-bold ${v.cls}`}>
          {v.label}
        </span>
      </div>
      <p className="mt-1 text-[12.5px] leading-snug text-stone-500 dark:text-stone-400">{c.outcome}</p>
    </li>
  );
}

function ReportCard({
  report,
  onRun,
  running,
}: {
  report: EvalReport | null;
  onRun: () => void;
  running: boolean;
}) {
  if (!report) {
    return (
      <div className="grid place-items-center rounded-2xl bg-white px-6 py-14 text-center ring-1 ring-stone-100 dark:bg-stone-900 dark:ring-stone-800">
        <ShieldIcon className="h-7 w-7 text-stone-300 dark:text-stone-600" />
        <p className="mt-3 text-[13px] font-semibold text-stone-500 dark:text-stone-400">
          Grade the guardrails
        </p>
        <p className="mt-1 mb-4 max-w-[300px] text-[12px] leading-snug text-stone-400 dark:text-stone-500">
          Runs a fixed set of scenarios through the AI and checks whether each guardrail holds for
          the current settings. Deterministic, offline, and free. Flip a switch on the Tinker tab,
          run it again, and watch the score change.
        </p>
        <RunEvalsButton onRun={onRun} running={running} />
      </div>
    );
  }

  const s = report.summary;
  const allGreen = s.failed === 0 && s.disabled === 0;
  const badge = (on: boolean) =>
    on
      ? 'bg-green-50 text-green-600 dark:bg-green-500/15 dark:text-green-400'
      : 'bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300';

  return (
    <div className="space-y-4">
      <section className="rounded-2xl bg-white p-4 ring-1 ring-stone-100 dark:bg-stone-900 dark:ring-stone-800">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-[22px] font-bold tnum text-stone-900 dark:text-stone-100">
              {s.holding} <span className="text-stone-300 dark:text-stone-600">/</span> {s.total}
            </div>
            <div className="text-[12.5px] font-semibold text-stone-500 dark:text-stone-400">
              guardrails currently holding
            </div>
          </div>
          <RunEvalsButton onRun={onRun} running={running} subtle />
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5 text-[11px] font-bold">
          <span className={`rounded-full px-2.5 py-1 ${badge(report.settings.guardrails)}`}>
            grounding {report.settings.guardrails ? 'on' : 'off'}
          </span>
          <span className={`rounded-full px-2.5 py-1 ${badge(report.settings.on_task)}`}>
            stay-on-task {report.settings.on_task ? 'on' : 'off'}
          </span>
          {s.disabled > 0 && (
            <span className="rounded-full bg-amber-50 px-2.5 py-1 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300">
              {s.disabled} disabled
            </span>
          )}
          {s.failed > 0 && (
            <span className="rounded-full bg-rose-50 px-2.5 py-1 text-rose-500 dark:bg-rose-500/15 dark:text-rose-400">
              {s.failed} failing
            </span>
          )}
          {allGreen && (
            <span className="rounded-full bg-green-50 px-2.5 py-1 text-green-600 dark:bg-green-500/15 dark:text-green-400">
              all guardrails holding
            </span>
          )}
        </div>
      </section>

      <ul className="space-y-2">
        {report.cases.map((c) => (
          <CaseRow key={c.id} c={c} />
        ))}
      </ul>
    </div>
  );
}
