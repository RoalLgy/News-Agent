export type Preferences = {
  schema_version: 1
  user: { name: string; email: string }
  subscription: {
    topics: string[]
    keywords: string[]
    exclude_keywords: string[]
    gdelt_keywords: string[]
    languages: ('zh' | 'en')[]
    max_items: number
    lookback_hours: number
  }
  delivery: {
    daily_enabled: boolean
    email_enabled: boolean
    time: string
    timezone: 'Asia/Shanghai'
  }
  updated_at: string
}
export type Delivery = { status: string; error?: string; recipient?: string }
export type BriefingSummary = {
  id: string
  title: string
  overview: string
  mode: string
  created_at: string
  delivery: Delivery
  item_count: number
  tool_call_count: number
}
export type Briefing = BriefingSummary & {
  items: {
    title: string
    summary: string
    why_it_matters: string
    sources: {
      article_id: string
      title: string
      publisher: string
      url: string
      published_at: string
    }[]
  }[]
  warnings: string[]
  lookback_hours: number
  agent_used: boolean
}
export type Source = {
  id: string
  kind: string
  name: string
  enabled: boolean
  interval_minutes: number
  url: string
  request_interval_seconds: number
}
export type Schedule = {
  schema_version: 1
  retention_days: number
  run_retention_days: number
  cleanup_time: string
  max_queries: number
  max_extract_per_run: number
  max_candidates: number
}
export type Config = { sources: { schema_version: 1; sources: Source[] }; scheduler: Schedule }
export type Status = {
  article_count: number
  extracted_count: number
  dated_count: number
  briefing_count: number
  model_configured: boolean
  mail_configured: boolean
  collector: {
    sources?: Record<string, { last_success?: string; errors: string[]; found: number }>
  }
}
export type Run = {
  id: string
  kind: string
  status: string
  created_at: string
  error?: string
  result?: { briefing_id?: string; new?: number; delivery?: Delivery }
}
export type News = {
  id: string
  title: string
  url: string
  publisher: string
  published_at: string | null
  first_seen_at: string
  snippet: string
  content_status: string
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail =
      typeof body.detail === 'string'
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail.map((e: { msg: string }) => e.msg).join('；')
          : '请求失败，请稍后重试'
    throw new Error(detail)
  }
  return response.json()
}
export const dateTime = (value?: string | null) =>
  value
    ? new Date(value).toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    : '尚无记录'
export const safeURL = (url: string) => (/^https?:\/\//i.test(url) ? url : '#')
export const splitTerms = (value: string) =>
  value
    .split(/[,，\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
