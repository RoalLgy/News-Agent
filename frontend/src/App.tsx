import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronRight,
  CircleHelp,
  Clock3,
  Database,
  FileText,
  Globe2,
  LoaderCircle,
  Mail,
  Radio,
  RefreshCw,
  Rss,
  Search,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  Sun,
  Terminal,
  X,
} from 'lucide-react'
import {
  api,
  dateTime,
  safeURL,
  splitTerms,
  type Briefing,
  type BriefingSummary,
  type Config,
  type News,
  type Preferences,
  type Run,
  type Status,
} from './types'

const tabs = [
  { id: 'briefings', label: '我的简报', icon: BookOpen },
  { id: 'preferences', label: '订阅偏好', icon: SlidersHorizontal },
  { id: 'news', label: '新闻资料库', icon: Database },
  { id: 'settings', label: '采集与运行', icon: Settings2 },
] as const
type Tab = (typeof tabs)[number]['id']
const statusLabels: Record<string, string> = {
  completed: '已完成',
  running: '运行中',
  queued: '排队中',
  failed: '失败',
  skipped: '已跳过',
  accepted: '邮件已提交',
  not_requested: '网页保存',
  sending: '发送中',
}

export default function App() {
  const [tab, setTab] = useState<Tab>('briefings')
  const [preferences, setPreferences] = useState<Preferences | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [config, setConfig] = useState<Config | null>(null)
  const [briefings, setBriefings] = useState<BriefingSummary[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [active, setActive] = useState<Run | null>(null)
  const [detail, setDetail] = useState<Briefing | null>(null)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [starting, setStarting] = useState(false)
  const [news, setNews] = useState<{ total: number; items: News[] }>({ total: 0, items: [] })
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const activeRef = useRef(active)
  activeRef.current = active
  const notify = useCallback((text: string, error = false) => setNotice({ text, error }), [])

  const refresh = useCallback(
    async (initial = false) => {
      try {
        const [s, b, r] = await Promise.all([
          api<Status>('/status'),
          api<BriefingSummary[]>('/briefings'),
          api<Run[]>('/runs'),
        ])
        setStatus(s)
        setBriefings(b)
        setRuns(r)
        if (initial) {
          const [p, c] = await Promise.all([
            api<Preferences>('/preferences'),
            api<Config>('/config'),
          ])
          setPreferences(p)
          setConfig(c)
        }
      } catch (e) {
        notify((e as Error).message, true)
      } finally {
        setLoading(false)
      }
    },
    [notify],
  )

  useEffect(() => {
    void refresh(true)
  }, [refresh])
  useEffect(() => {
    if (!notice) return
    const t = setTimeout(() => setNotice(null), 6500)
    return () => clearTimeout(t)
  }, [notice])
  useEffect(() => {
    const timer = setInterval(() => {
      const current = activeRef.current
      if (!current) {
        void refresh()
        return
      }
      void api<Run>(`/runs/${current.id}`)
        .then(async (run) => {
          if (['completed', 'failed', 'skipped'].includes(run.status)) {
            setActive(null)
            if (run.status === 'completed') {
              notify(
                run.result?.delivery?.status === 'failed'
                  ? '简报已保存，但邮件发送失败，请查看详情重试。'
                  : '任务已完成，内容已更新。',
                run.result?.delivery?.status === 'failed',
              )
              if (run.result?.briefing_id) {
                setDetail(await api<Briefing>(`/briefings/${run.result.briefing_id}`))
                setTab('briefings')
              }
            } else notify(run.error || '任务未完成', true)
            await refresh()
          } else setActive(run)
        })
        .catch((e) => notify(e.message, true))
    }, 3000)
    return () => clearInterval(timer)
  }, [notify, refresh])
  useEffect(() => {
    if (tab !== 'news') return
    let current = true
    const timer = setTimeout(
      () =>
        void api<{ total: number; items: News[] }>(
          `/news?q=${encodeURIComponent(query)}&offset=${offset}`,
        )
          .then((data) => {
            if (current) setNews(data)
          })
          .catch((e) => notify(e.message, true)),
      200,
    )
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [tab, query, offset, status?.article_count, notify])

  async function start(kind: 'collect' | 'generate' | 'demo') {
    setStarting(true)
    try {
      setActive(
        await api<Run>(`/${kind}`, { method: 'POST', body: JSON.stringify({ send: false }) }),
      )
    } catch (e) {
      notify((e as Error).message, true)
    } finally {
      setStarting(false)
    }
  }
  async function savePreferences(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    try {
      setPreferences(
        await api<Preferences>('/preferences', {
          method: 'PUT',
          body: JSON.stringify(preferences),
        }),
      )
      notify('偏好已保存。后台开启时会按新订阅补采新闻。')
    } catch (e) {
      notify((e as Error).message, true)
    } finally {
      setSaving(false)
    }
  }
  async function saveConfig(event: FormEvent) {
    event.preventDefault()
    if (!config) return
    setSaving(true)
    try {
      await api('/sources', { method: 'PUT', body: JSON.stringify(config.sources) })
      await api('/schedule', { method: 'PUT', body: JSON.stringify(config.scheduler) })
      notify('采集设置已保存，下一次调度检查生效。')
    } catch (e) {
      notify((e as Error).message, true)
    } finally {
      setSaving(false)
    }
  }
  const busy = Boolean(active) || starting
  const lastCollection = Object.values(status?.collector.sources || {})
    .map((s) => s.last_success)
    .filter(Boolean)
    .sort()
    .at(-1)
  const now = new Date()
  const greeting =
    preferences?.user.name && preferences.user.name !== '我的订阅'
      ? `${preferences.user.name}，`
      : ''

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="新闻简报首页">
          <span className="brand-symbol">
            <Sun size={24} strokeWidth={1.6} />
          </span>
          <span>
            新闻简报<span className="brand-en">NEWS BRIEF DAILY</span>
          </span>
        </a>
        <div className="sidebar-caption">你的每日信息坐标</div>
        <nav aria-label="主导航">
          {tabs.map((t) => (
            <button
              key={t.id}
              aria-label={t.label}
              className={`nav-item ${tab === t.id ? 'active' : ''}`}
              onClick={() => {
                setTab(t.id)
                setDetail(null)
              }}
            >
              <t.icon size={18} />
              <span>{t.label}</span>
              {tab === t.id && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="small-orbit">
            <Sparkles size={17} />
          </span>
          <strong>让好奇心，有所抵达。</strong>
          <p>
            从纷繁资讯中，
            <br />
            找到与你有关的变化。
          </p>
          <button onClick={() => setTab('preferences')}>
            定制我的关注 <ArrowUpRight size={14} />
          </button>
        </div>
        <div className="sidebar-bottom">
          <span className="connection-dot" />
          <span>本地工作空间</span>
          <span className="version">v0.1</span>
        </div>
      </aside>

      <div className="main-shell">
        <header className="topbar">
          <div>
            <span className="breadcrumb">工作空间</span>
            <ChevronRight size={13} />
            <span>{tabs.find((t) => t.id === tab)?.label}</span>
          </div>
          <span className="today">
            <Sun size={15} />
            {now.toLocaleDateString('zh-CN', {
              timeZone: 'Asia/Shanghai',
              month: 'long',
              day: 'numeric',
              weekday: 'long',
            })}
          </span>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <div className="eyebrow">A LITTLE LESS NOISE. A LITTLE MORE INSIGHT.</div>
              <h1>
                {tab === 'briefings'
                  ? `${greeting}今天值得关注什么？`
                  : tabs.find((t) => t.id === tab)?.label}
              </h1>
              <p>
                {tab === 'briefings'
                  ? '你关心的世界，我们每天为你整理。'
                  : tab === 'preferences'
                    ? '设置你的关注兴趣，让每一份简报更贴近你。'
                    : tab === 'news'
                      ? '所有发现都有出处，所有简报都有依据。'
                      : '按自己的节奏，保持新闻资料新鲜。'}
              </p>
            </div>
            <span className="workspace-badge">
              <span className="connection-dot" /> JSON 文件存储
            </span>
          </div>
          {active && (
            <div className="task-banner" role="status">
              <LoaderCircle size={18} className="spin" />
              <span>
                {active.kind === 'collect'
                  ? '正在检索并提取新闻，可能需要几分钟…'
                  : '正在整理简报，请稍候…'}
              </span>
              <code>{active.id.slice(-8)}</code>
            </div>
          )}
          {loading ? (
            <div className="loading">
              <LoaderCircle className="spin" /> 正在打开你的工作空间…
            </div>
          ) : (
            <>
              {tab === 'briefings' &&
                (detail ? (
                  <BriefingDetail
                    briefing={detail}
                    onBack={() => setDetail(null)}
                    onUpdate={setDetail}
                    notify={notify}
                  />
                ) : (
                  <>
                    <section className="hero">
                      <div className="hero-content">
                        <div className="hero-label">
                          <span /> PERSONAL INTELLIGENCE, DAILY
                        </div>
                        <h2>
                          世界在更新。
                          <br />
                          把注意力，留给重要的事。
                        </h2>
                        <p>
                          聚合多渠道新闻，按你的订阅筛选，
                          <br className="desktop-break" />
                          生成一份有来源、有重点的专属简报。
                        </p>
                        <div className="hero-actions">
                          <button
                            className="button button-cream"
                            disabled={busy}
                            onClick={() => void start('generate')}
                          >
                            <Sparkles size={16} />
                            生成今日简报
                            <ArrowRight size={16} />
                          </button>
                          <button
                            className="hero-link"
                            disabled={busy}
                            onClick={() => void start('collect')}
                          >
                            <RefreshCw size={15} />
                            立即抓取新闻
                          </button>
                        </div>
                      </div>
                      <div className="hero-art" aria-hidden="true">
                        <div className="orbit orbit-one" />
                        <div className="orbit orbit-two" />
                        <div className="orbit orbit-three" />
                        <div className="sun-disc" />
                        <div className="art-star star-one">✦</div>
                        <div className="art-star star-two">✧</div>
                        <div className="paper paper-back" />
                        <div className="paper paper-front">
                          <span className="paper-logo">N / DAILY</span>
                          <div className="paper-rule" />
                          <div className="paper-title" />
                          <div className="paper-title short" />
                          <div className="paper-image">
                            <Globe2 size={46} strokeWidth={1} />
                          </div>
                          <div className="paper-lines" />
                          <div className="paper-lines" />
                          <div className="paper-footer">
                            CURATED FOR YOU <ArrowUpRight size={13} />
                          </div>
                        </div>
                        <span className="art-caption">STAY CURIOUS. STAY INFORMED.</span>
                      </div>
                    </section>
                    <section className="stats-grid" aria-label="工作空间统计">
                      <Stat
                        icon={<Rss size={19} />}
                        label="已收集新闻"
                        value={status?.article_count || 0}
                        suffix="篇"
                        note={`${status?.dated_count || 0} 篇具有发布时间`}
                      />
                      <Stat
                        icon={<FileText size={19} />}
                        label="历史简报"
                        value={briefings.length}
                        suffix="份"
                        note="每一份都为你保留"
                      />
                      <Stat
                        icon={<Clock3 size={19} />}
                        label="上次采集"
                        value={lastCollection ? dateTime(lastCollection) : '等待首次采集'}
                        note="按小时自动更新资料"
                      />
                    </section>
                    <section className="history-section">
                      <div className="section-heading">
                        <div>
                          <h2>
                            简报档案 <span>{briefings.length.toString().padStart(2, '0')}</span>
                          </h2>
                          <p>值得回看的信息，随时都在。</p>
                        </div>
                        <button className="text-button" onClick={() => void refresh()}>
                          <RefreshCw size={14} />
                          刷新
                        </button>
                      </div>
                      {briefings.length ? (
                        <div className="briefing-grid">
                          {briefings.map((b, index) => (
                            <button
                              key={b.id}
                              className="briefing-card"
                              onClick={() =>
                                void api<Briefing>(`/briefings/${b.id}`)
                                  .then(setDetail)
                                  .catch((e) => notify(e.message, true))
                              }
                            >
                              <div className="card-top">
                                <span className={b.mode === 'demo' ? 'tag tag-amber' : 'tag'}>
                                  {b.mode === 'demo'
                                    ? '演示简报'
                                    : index === 0
                                      ? '最新简报'
                                      : '每日简报'}
                                </span>
                                <span>{dateTime(b.created_at)}</span>
                              </div>
                              <h3>{b.title}</h3>
                              <p>{b.overview}</p>
                              <div className="card-bottom">
                                <span>
                                  <FileText size={14} />
                                  {b.item_count} 条资讯<span className="divider">/</span>
                                  {statusLabels[b.delivery.status] || b.delivery.status}
                                </span>
                                <ArrowUpRight size={18} />
                              </div>
                            </button>
                          ))}
                        </div>
                      ) : (
                        <div className="empty-state">
                          <div className="empty-icon">
                            <BookOpen size={27} strokeWidth={1.3} />
                          </div>
                          <h3>第一份简报，从你的好奇心开始</h3>
                          <p>先设置关注偏好，再抓取新闻，生成你的专属简报。</p>
                          <div>
                            <button
                              className="button button-primary"
                              onClick={() => setTab('preferences')}
                            >
                              设置我的偏好
                              <ArrowRight size={15} />
                            </button>
                            <button
                              className="text-button"
                              disabled={busy}
                              onClick={() => void start('demo')}
                            >
                              先看一份演示
                              <ArrowUpRight size={14} />
                            </button>
                          </div>
                        </div>
                      )}
                    </section>
                    <div className="bottom-tip">
                      <CircleHelp size={16} />
                      <span>
                        {status?.model_configured
                          ? '模型已配置，简报生成时将调用 LangChain Agent。'
                          : '首次使用？可先查看演示；正式生成需在 .env 中配置模型密钥。'}
                      </span>
                      <button onClick={() => setTab('settings')}>
                        查看运行状态 <ArrowRight size={13} />
                      </button>
                    </div>
                  </>
                ))}

              {tab === 'preferences' && preferences && (
                <form className="settings-form" onSubmit={savePreferences}>
                  <section className="panel">
                    <div className="panel-heading">
                      <span className="panel-number">01</span>
                      <div>
                        <h2>你关心什么</h2>
                        <p>用逗号或换行分隔多个话题和关键词。</p>
                      </div>
                    </div>
                    <div className="quick-topics">
                      {['人工智能', '财经', '新能源汽车', '国际', '体育', '科技'].map((topic) => (
                        <button
                          type="button"
                          key={topic}
                          className={`topic-chip ${preferences.subscription.topics.includes(topic) ? 'selected' : ''}`}
                          onClick={() =>
                            setPreferences({
                              ...preferences,
                              subscription: {
                                ...preferences.subscription,
                                topics: preferences.subscription.topics.includes(topic)
                                  ? preferences.subscription.topics.filter((t) => t !== topic)
                                  : [...preferences.subscription.topics, topic],
                              },
                            })
                          }
                        >
                          {preferences.subscription.topics.includes(topic) && <Check size={13} />}
                          {topic}
                        </button>
                      ))}
                    </div>
                    <Terms
                      label="关注话题"
                      values={preferences.subscription.topics}
                      onChange={(topics) =>
                        setPreferences({
                          ...preferences,
                          subscription: { ...preferences.subscription, topics },
                        })
                      }
                    />
                    <Terms
                      label="重点关键词"
                      hint="公司、人物、产品或具体事件"
                      values={preferences.subscription.keywords}
                      onChange={(keywords) =>
                        setPreferences({
                          ...preferences,
                          subscription: { ...preferences.subscription, keywords },
                        })
                      }
                    />
                    <Terms
                      label="排除关键词"
                      values={preferences.subscription.exclude_keywords}
                      onChange={(exclude_keywords) =>
                        setPreferences({
                          ...preferences,
                          subscription: { ...preferences.subscription, exclude_keywords },
                        })
                      }
                    />
                    <Terms
                      label="GDELT 英文检索词（可选）"
                      hint="常见话题会自动映射；其他中文专有词可在此补充英文名称。"
                      values={preferences.subscription.gdelt_keywords}
                      onChange={(gdelt_keywords) =>
                        setPreferences({
                          ...preferences,
                          subscription: { ...preferences.subscription, gdelt_keywords },
                        })
                      }
                    />
                    <div className="form-row">
                      <label>
                        每份简报条数
                        <input
                          type="number"
                          min="1"
                          max="20"
                          value={preferences.subscription.max_items}
                          onChange={(e) =>
                            setPreferences({
                              ...preferences,
                              subscription: {
                                ...preferences.subscription,
                                max_items: Number(e.target.value),
                              },
                            })
                          }
                        />
                      </label>
                      <label>
                        新闻时间范围
                        <select
                          value={preferences.subscription.lookback_hours}
                          onChange={(e) =>
                            setPreferences({
                              ...preferences,
                              subscription: {
                                ...preferences.subscription,
                                lookback_hours: Number(e.target.value),
                              },
                            })
                          }
                        >
                          {[12, 24, 48, 72, 168].map((h) => (
                            <option key={h} value={h}>
                              最近 {h} 小时
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    <div className="language-row">
                      新闻语言
                      {(['zh', 'en'] as const).map((lang) => (
                        <label className="inline-check" key={lang}>
                          <input
                            type="checkbox"
                            checked={preferences.subscription.languages.includes(lang)}
                            onChange={(e) =>
                              setPreferences({
                                ...preferences,
                                subscription: {
                                  ...preferences.subscription,
                                  languages: e.target.checked
                                    ? [...preferences.subscription.languages, lang]
                                    : preferences.subscription.languages.filter((l) => l !== lang),
                                },
                              })
                            }
                          />
                          {lang === 'zh' ? '中文' : '英文'}
                        </label>
                      ))}
                    </div>
                  </section>
                  <section className="panel">
                    <div className="panel-heading">
                      <span className="panel-number">02</span>
                      <div>
                        <h2>让简报按时抵达</h2>
                        <p>无须登录，身份和配置仅保存在本地。</p>
                      </div>
                    </div>
                    <div className="form-row">
                      <label>
                        你的称呼
                        <input
                          value={preferences.user.name}
                          required
                          maxLength={80}
                          onChange={(e) =>
                            setPreferences({
                              ...preferences,
                              user: { ...preferences.user, name: e.target.value },
                            })
                          }
                        />
                      </label>
                      <label>
                        接收邮箱（可选）
                        <input
                          type="email"
                          placeholder="you@example.com"
                          value={preferences.user.email}
                          onChange={(e) =>
                            setPreferences({
                              ...preferences,
                              user: { ...preferences.user, email: e.target.value },
                            })
                          }
                        />
                      </label>
                    </div>
                    <label className="switch-row">
                      <span>
                        <strong>每日自动生成</strong>
                        <small>服务运行时，在北京时间定时生成。</small>
                      </span>
                      <input
                        role="switch"
                        type="checkbox"
                        checked={preferences.delivery.daily_enabled}
                        onChange={(e) =>
                          setPreferences({
                            ...preferences,
                            delivery: { ...preferences.delivery, daily_enabled: e.target.checked },
                          })
                        }
                      />
                    </label>
                    <label className="switch-row">
                      <span>
                        <strong>邮件推送</strong>
                        <small>需要在服务端配置 Resend 和发件地址。</small>
                      </span>
                      <input
                        role="switch"
                        type="checkbox"
                        checked={preferences.delivery.email_enabled}
                        onChange={(e) =>
                          setPreferences({
                            ...preferences,
                            delivery: { ...preferences.delivery, email_enabled: e.target.checked },
                          })
                        }
                      />
                    </label>
                    <label className="time-field">
                      每日生成时间 · 北京时间
                      <input
                        type="time"
                        required
                        value={preferences.delivery.time}
                        onChange={(e) =>
                          setPreferences({
                            ...preferences,
                            delivery: { ...preferences.delivery, time: e.target.value },
                          })
                        }
                      />
                    </label>
                  </section>
                  <div className="form-footer">
                    <span>保存后，前端与命令行共享这份偏好。</span>
                    <button className="button button-primary" disabled={saving}>
                      {saving ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}
                      保存订阅偏好
                    </button>
                  </div>
                </form>
              )}

              {tab === 'news' && (
                <>
                  <div className="search-toolbar">
                    <div className="search-input">
                      <Search size={17} />
                      <input
                        aria-label="搜索新闻"
                        placeholder="搜索标题、摘要或正文…"
                        value={query}
                        onChange={(e) => {
                          setQuery(e.target.value)
                          setOffset(0)
                        }}
                      />
                    </div>
                    <button
                      className="button button-primary"
                      disabled={busy}
                      onClick={() => void start('collect')}
                    >
                      <RefreshCw size={15} />
                      立即抓取
                    </button>
                  </div>
                  <div className="section-heading">
                    <p>共 {news.total} 篇新闻 · 无可靠发布时间的内容默认不进入简报</p>
                  </div>
                  <section className="panel news-list">
                    {news.items.length ? (
                      news.items.map((n) => (
                        <article key={n.id} className="news-row">
                          <div>
                            <span className="news-source">
                              {n.publisher || '来源待解析'} ·{' '}
                              {n.published_at ? dateTime(n.published_at) : '发布时间待确认'}
                            </span>
                            <h3>
                              <a href={safeURL(n.url)} target="_blank" rel="noopener noreferrer">
                                {n.title}
                                <ArrowUpRight size={14} />
                              </a>
                            </h3>
                            <p>{n.snippet || '已保存新闻线索，正文等待提取。'}</p>
                          </div>
                          <span
                            className={`tag ${n.content_status === 'extracted' ? '' : 'tag-neutral'}`}
                          >
                            {n.content_status === 'extracted' ? '已有正文' : '摘要 / 线索'}
                          </span>
                        </article>
                      ))
                    ) : (
                      <div className="compact-empty">
                        <Rss size={28} />
                        <h3>暂无匹配新闻</h3>
                        <p>检查订阅偏好并执行一次采集，新闻将保存在本地 JSON 文件中。</p>
                      </div>
                    )}
                  </section>
                  <div className="pagination">
                    <button
                      className="text-button"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - 30))}
                    >
                      上一页
                    </button>
                    <span>第 {Math.floor(offset / 30) + 1} 页</span>
                    <button
                      className="text-button"
                      disabled={offset + 30 >= news.total}
                      onClick={() => setOffset(offset + 30)}
                    >
                      下一页
                    </button>
                  </div>
                </>
              )}

              {tab === 'settings' && config && (
                <>
                  <div className="runtime-grid">
                    <div className="runtime-card">
                      <Sparkles size={20} />
                      <div>
                        <strong>LangChain 模型</strong>
                        <span>
                          {status?.model_configured ? '已配置密钥' : '待配置 OPENAI_API_KEY'}
                        </span>
                      </div>
                      <span
                        className={`connection-dot ${!status?.model_configured ? 'muted' : ''}`}
                      />
                    </div>
                    <div className="runtime-card">
                      <Mail size={20} />
                      <div>
                        <strong>邮件推送</strong>
                        <span>
                          {status?.mail_configured ? '发件服务已配置' : '待配置 Resend 和发件地址'}
                        </span>
                      </div>
                      <span
                        className={`connection-dot ${!status?.mail_configured ? 'muted' : ''}`}
                      />
                    </div>
                  </div>
                  <form className="settings-form" onSubmit={saveConfig}>
                    <section className="panel">
                      <div className="panel-heading">
                        <span className="panel-number">
                          <Globe2 size={18} />
                        </span>
                        <div>
                          <h2>新闻发现渠道</h2>
                          <p>免费来源按频率采集；来源可用性取决于部署网络与上游服务。</p>
                        </div>
                      </div>
                      {config.sources.sources.map((source, index) => (
                        <div className="source-row" key={source.id}>
                          <Rss size={19} />
                          <div className="source-info">
                            <strong>{source.name}</strong>
                            <small>
                              {status?.collector.sources?.[source.id]?.errors?.length
                                ? '最近采集失败，可在任务记录查看原因'
                                : `上次成功：${dateTime(status?.collector.sources?.[source.id]?.last_success)}`}
                            </small>
                          </div>
                          <select
                            aria-label={`${source.name}采集频率`}
                            value={source.interval_minutes}
                            onChange={(e) =>
                              setConfig({
                                ...config,
                                sources: {
                                  ...config.sources,
                                  sources: config.sources.sources.map((s, i) =>
                                    i === index
                                      ? { ...s, interval_minutes: Number(e.target.value) }
                                      : s,
                                  ),
                                },
                              })
                            }
                          >
                            {[30, 60, 120, 240].map((m) => (
                              <option value={m} key={m}>
                                每 {m < 60 ? `${m} 分钟` : `${m / 60} 小时`}
                              </option>
                            ))}
                          </select>
                          <input
                            type="checkbox"
                            aria-label={`启用${source.name}`}
                            checked={source.enabled}
                            onChange={(e) =>
                              setConfig({
                                ...config,
                                sources: {
                                  ...config.sources,
                                  sources: config.sources.sources.map((s, i) =>
                                    i === index ? { ...s, enabled: e.target.checked } : s,
                                  ),
                                },
                              })
                            }
                          />
                        </div>
                      ))}
                      <div className="form-row settings-numbers">
                        <label>
                          新闻保留天数
                          <input
                            type="number"
                            min="1"
                            max="90"
                            value={config.scheduler.retention_days}
                            onChange={(e) =>
                              setConfig({
                                ...config,
                                scheduler: {
                                  ...config.scheduler,
                                  retention_days: Number(e.target.value),
                                },
                              })
                            }
                          />
                        </label>
                        <label>
                          每天清理时间 · 北京时间
                          <input
                            type="time"
                            required
                            value={config.scheduler.cleanup_time}
                            onChange={(e) =>
                              setConfig({
                                ...config,
                                scheduler: { ...config.scheduler, cleanup_time: e.target.value },
                              })
                            }
                          />
                        </label>
                      </div>
                      <p className="field-hint">
                        清理原始新闻不会删除历史简报，引用内容已保留快照。
                      </p>
                    </section>
                    <div className="form-footer">
                      <span>密钥保存在 .env，不会通过页面返回。</span>
                      <button className="button button-primary" disabled={saving}>
                        <Check size={16} />
                        保存采集设置
                      </button>
                    </div>
                  </form>
                  <section className="panel runs-panel">
                    <div className="section-heading">
                      <h2>
                        <Radio size={18} />
                        最近任务
                      </h2>
                      <button className="text-button" onClick={() => void refresh()}>
                        <RefreshCw size={14} />
                        刷新
                      </button>
                    </div>
                    {runs.length ? (
                      runs.slice(0, 12).map((r) => (
                        <div className="run-row" key={r.id}>
                          <span className={`run-dot ${r.status}`} />
                          <span>
                            {(
                              {
                                collect: '新闻采集',
                                generate: '简报生成',
                                cleanup: '过期清理',
                                demo: '演示生成',
                              } as Record<string, string>
                            )[r.kind] || r.kind}
                            <small>{r.error || r.id.slice(-8)}</small>
                          </span>
                          <span className={`run-state ${r.status}`}>
                            {statusLabels[r.status] || r.status}
                          </span>
                          <time>{dateTime(r.created_at)}</time>
                        </div>
                      ))
                    ) : (
                      <p className="field-hint">暂无运行记录，开始一次采集或生成即可看到结果。</p>
                    )}
                  </section>
                  <div className="cli-tip">
                    <Terminal size={19} />
                    <div>
                      <strong>也可以从命令行开始</strong>
                      <code>
                        newsbrief collect &nbsp; → &nbsp; newsbrief generate &nbsp; → &nbsp;
                        newsbrief history
                      </code>
                    </div>
                  </div>
                </>
              )}
            </>
          )}
          <footer className="page-footer">
            <span>新闻简报 NEWS BRIEF DAILY</span>
            <span>追逐信息，也守护注意力。</span>
            <span>本地优先 · 来源可溯</span>
          </footer>
        </main>
      </div>
      {notice && (
        <div role="alert" className={`toast ${notice.error ? 'error' : ''}`}>
          {notice.error ? <CircleHelp size={18} /> : <Check size={18} />}
          <span>{notice.text}</span>
          <button onClick={() => setNotice(null)} aria-label="关闭提示">
            <X size={16} />
          </button>
        </div>
      )}
    </div>
  )
}

function Stat({
  icon,
  label,
  value,
  suffix,
  note,
}: {
  icon: React.ReactNode
  label: string
  value: number | string
  suffix?: string
  note: string
}) {
  return (
    <div className="stat-card">
      <div className="stat-heading">
        <span>{label}</span>
        {icon}
      </div>
      <div className={`stat-value ${typeof value === 'string' ? 'stat-text' : ''}`}>
        {value}
        <small>{suffix}</small>
      </div>
      <p>{note}</p>
    </div>
  )
}

function Terms({
  label,
  hint,
  values,
  onChange,
}: {
  label: string
  hint?: string
  values: string[]
  onChange: (values: string[]) => void
}) {
  const [raw, setRaw] = useState(values.join('，'))
  useEffect(() => {
    setRaw(values.join('，'))
  }, [values])
  return (
    <label>
      {label}
      <input
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        onBlur={() => onChange(splitTerms(raw))}
        placeholder="用逗号分隔，例如 人工智能，新能源"
      />
      {hint && <small className="field-hint">{hint}</small>}
    </label>
  )
}

function BriefingDetail({
  briefing: b,
  onBack,
  onUpdate,
  notify,
}: {
  briefing: Briefing
  onBack: () => void
  onUpdate: (b: Briefing) => void
  notify: (s: string, error?: boolean) => void
}) {
  const [sending, setSending] = useState(false)
  async function send() {
    setSending(true)
    try {
      const delivery = await api<Briefing['delivery']>(`/briefings/${b.id}/send`, {
        method: 'POST',
      })
      onUpdate({ ...b, delivery })
      notify(
        delivery.status === 'accepted' ? '邮件已提交给服务商。' : delivery.error || '邮件发送失败',
        delivery.status !== 'accepted',
      )
    } catch (e) {
      notify((e as Error).message, true)
    } finally {
      setSending(false)
    }
  }
  return (
    <section className="detail">
      <div className="detail-toolbar">
        <button className="text-button" onClick={onBack}>
          ← 返回简报档案
        </button>
        <div>
          <a className="text-button" href={`/api/briefings/${b.id}/markdown`} download>
            <ArrowDownToLine size={15} />
            导出 Markdown
          </a>
          {b.mode !== 'demo' && (
            <button
              className="text-button"
              disabled={sending || b.delivery.status === 'accepted'}
              onClick={() => void send()}
            >
              <Mail size={15} />
              {b.delivery.status === 'accepted' ? '邮件已提交' : sending ? '发送中…' : '发送邮件'}
            </button>
          )}
        </div>
      </div>
      <article className="briefing-paper">
        <div className="detail-kicker">
          <Sun size={18} /> NEWS BRIEF / YOUR DAILY BRIEF<span>{dateTime(b.created_at)}</span>
        </div>
        <h2>{b.title}</h2>
        {b.mode === 'demo' && (
          <div className="demo-notice">演示内容 · 以下为虚构示例，未调用真实新闻来源和模型。</div>
        )}
        <p className="overview">{b.overview}</p>
        <div className="briefing-meta">
          <span>
            <Clock3 size={14} />
            最近 {b.lookback_hours} 小时
          </span>
          <span>
            <FileText size={14} />
            {b.items.length} 条资讯
          </span>
          {b.agent_used && (
            <span>
              <Sparkles size={14} />
              {b.tool_call_count} 次工具调用
            </span>
          )}
        </div>
        {b.items.map((item, index) => (
          <section className="briefing-item" key={index}>
            <span className="item-number">{(index + 1).toString().padStart(2, '0')}</span>
            <div>
              <h3>{item.title}</h3>
              <p>{item.summary}</p>
              {item.why_it_matters && (
                <div className="why">
                  <span>与你有关</span>
                  {item.why_it_matters}
                </div>
              )}
              <div className="sources">
                {item.sources.map((source) => (
                  <a
                    key={source.article_id}
                    href={safeURL(source.url)}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {source.publisher || source.title}
                    <ArrowUpRight size={12} />
                    <span>{dateTime(source.published_at)}</span>
                  </a>
                ))}
              </div>
            </div>
          </section>
        ))}
        {b.warnings?.map((w) => (
          <p className="detail-warning" key={w}>
            <CircleHelp size={15} />
            {w}
          </p>
        ))}
        {b.delivery.status === 'failed' && (
          <p className="detail-warning">邮件发送失败：{b.delivery.error}</p>
        )}
        <div className="detail-end">— 保持好奇，明天见。—</div>
      </article>
    </section>
  )
}
