// Shared type definitions for the Abeg web app.

/* ------------------------------------------------------------------ */
/* Catalog / products                                                  */
/* ------------------------------------------------------------------ */
export interface Product {
  sku: string;
  name: string;
  price: number;
  qty_on_hand: number;
  available: number;
}

export interface CatalogMeta {
  emoji: string;
  gradient: string;
  blurb: string;
}

export interface StockTag {
  label: string;
  tone: 'amber' | 'green' | 'stone';
  soldOut: boolean;
}

/* ------------------------------------------------------------------ */
/* Chat                                                                */
/* ------------------------------------------------------------------ */
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  streaming: boolean;
  status?: string | null;
  guardNote?: string | null;
  image?: string | null;
}

/* ------------------------------------------------------------------ */
/* Workshop / controls                                                 */
/* ------------------------------------------------------------------ */
export interface Script {
  n: number;
  message: string;
}

export interface Model {
  id: string;
  label: string;
  note: string;
}

export type TimelineTone = 'default' | 'refused' | 'order';

export interface TimelineItem {
  id: string;
  tone: TimelineTone;
  title: string;
  meta?: string;
}

// Body sent to the system_prompt control endpoint.
export interface SystemPromptBody {
  prompt?: string;
  reset?: boolean;
}

/* ------------------------------------------------------------------ */
/* Trace ("anatomy of the last answer")                                */
/* ------------------------------------------------------------------ */
export interface Tokens {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export type TraceStep =
  | { kind: 'user'; text: string }
  | { kind: 'decide'; count: number }
  | { kind: 'tool'; name: string; title: string; outcome: string; ms?: number | null }
  | { kind: 'reply'; text: string };

export interface Trace {
  steps: TraceStep[];
  model: string;
  temperature: number;
  cached: boolean;
  guardrails: boolean;
  grounding_blocked: boolean;
  on_task: boolean;
  on_task_blocked: boolean;
  bounded_hit: boolean;
  tool_calls: number;
  tokens?: Tokens | null;
  cost_usd?: number | null;
  ttft_ms?: number | null;
  total_ms?: number;
  context_messages: number;
}

/* ------------------------------------------------------------------ */
/* Report Card (guardrail eval suite)                                  */
/* ------------------------------------------------------------------ */
export type EvalStatus = 'held' | 'slipped' | 'grounded' | 'info' | 'failed';

export interface EvalCaseResult {
  id: string;
  title: string;
  guardrail: string;
  status: EvalStatus;
  outcome: string;
  detail?: string;
}

export interface EvalSummary {
  holding: number;
  total: number;
  disabled: number;
  failed: number;
}

export interface EvalReport {
  settings: {
    guardrails: boolean;
    on_task: boolean;
    cached_mode: boolean;
    model: string;
  };
  cases: EvalCaseResult[];
  summary: EvalSummary;
}

/* ------------------------------------------------------------------ */
/* Operator / SSE events                                               */
/* ------------------------------------------------------------------ */
export interface OrderItem {
  qty: number;
  sku: string;
}

// Result payload of a tool_result event; only a few fields are ever read.
export interface ToolResult {
  name?: string;
  sku?: string;
  refused?: boolean;
  error?: string;
  reason?: string;
  items?: OrderItem[];
}

export interface InventoryUpdateData {
  products?: Product[];
}

export interface StateData {
  guardrails?: boolean;
  cached_mode?: boolean;
  on_task?: boolean;
  temperature?: number;
  model?: string;
  models?: Model[];
  system_prompt?: string;
  default_system_prompt?: string;
  system_prompt_customized?: boolean;
}

export interface ToolResultData {
  name?: string;
  duration_ms?: number | null;
  result?: ToolResult;
}

export interface OrderCreatedData {
  items?: OrderItem[];
  reference?: string;
  total?: number;
}

export interface ReservationData {
  refused?: boolean;
  reason?: string;
}

export interface MessageData {
  message?: string;
}

export interface OnData {
  on?: boolean;
}

export interface ActivityData {
  state?: 'start' | 'end';
  label?: string;
}

export interface DeltaData {
  text?: string;
}

// Discriminated union of every operator/SSE event the app handles. The
// `data` payload is typed per variant; a `session_id` may accompany any event.
export type OperatorEvent = { session_id?: string } & (
  | { type: 'inventory_update'; data: InventoryUpdateData }
  | { type: 'state'; data: StateData }
  | { type: 'turn_trace'; data: Trace }
  | { type: 'guardrails'; data: OnData }
  | { type: 'cached'; data: OnData }
  | { type: 'tool_result'; data: ToolResultData }
  | { type: 'order_created'; data: OrderCreatedData }
  | { type: 'reservation'; data: ReservationData }
  | { type: 'notice'; data: MessageData }
  | { type: 'error'; data: MessageData }
  | { type: 'activity'; data: ActivityData }
  | { type: 'assistant_delta'; data: DeltaData }
  | { type: 'assistant_done'; data: DeltaData }
);

// Flattened view of an event's `data` used at consumption sites, where the
// code reads a handful of optional fields after switching on `type` separately.
export interface EventData {
  products?: Product[];
  guardrails?: boolean;
  cached_mode?: boolean;
  on_task?: boolean;
  temperature?: number;
  model?: string;
  models?: Model[];
  system_prompt?: string;
  default_system_prompt?: string;
  system_prompt_customized?: boolean;
  on?: boolean;
  name?: string;
  duration_ms?: number | null;
  result?: ToolResult;
  items?: OrderItem[];
  reference?: string;
  total?: number;
  refused?: boolean;
  reason?: string;
  message?: string;
  state?: 'start' | 'end';
  label?: string;
  text?: string;
}

/* ------------------------------------------------------------------ */
/* API responses                                                       */
/* ------------------------------------------------------------------ */
export interface AppState {
  guardrails?: boolean;
  cached_mode?: boolean;
  on_task?: boolean;
  temperature?: number;
  model?: string;
  models?: Model[];
  system_prompt?: string;
  default_system_prompt?: string;
  system_prompt_customized?: boolean;
}

export interface ProductsResponse {
  products?: Product[];
}
