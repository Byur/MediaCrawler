import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Heart,
  MessageCircle,
  RefreshCw,
  Save,
  Share2,
  Star,
} from 'lucide-react'
import { labelApi, type LabelPost, type LabelStats } from '@/lib/api'

/** 判据清单：与 docs/软广判据-heuristics.md 对应。权重只用于排序。 */
const SIGNALS: { key: string; label: string; hint: string; weight: number }[] = [
  {
    key: 'staging',
    label: 'A 摆拍图片',
    hint: '菜品数超过碗筷数 / 正对视角 / 看镜头 / 无使用痕迹',
    weight: 25,
  },
  {
    key: 'profile',
    label: 'B 主页历史',
    hint: '短时间跨城 / 全是探店 / 模板雷同',
    weight: 25,
  },
  {
    key: 'matrix',
    label: 'D 矩阵',
    hint: '评论区互评闭合 / 同批账号反复共现',
    weight: 25,
  },
  {
    key: 'language',
    label: 'C 语言风格',
    hint: '情绪高但零信息、零抱怨（仅作旁证）',
    weight: 15,
  },
  {
    key: 'ratio',
    label: 'E 互动比值',
    hint: '评论/赞 极低，需与同品类基线比较',
    weight: 10,
  },
]

const LEVELS: { value: string; label: string; className: string }[] = [
  { value: 'normal', label: '正常', className: 'text-cyber-neon-green border-cyber-neon-green/60 bg-cyber-neon-green/10' },
  { value: 'suspect', label: '疑似', className: 'text-cyber-neon-orange border-cyber-neon-orange/60 bg-cyber-neon-orange/10' },
  { value: 'strong', label: '强疑似', className: 'text-cyber-neon-pink border-cyber-neon-pink/60 bg-cyber-neon-pink/10' },
]

const STATE_LABELS = ['未见', '可疑', '明显']
const STATE_STYLES = [
  'text-cyber-text-muted border-cyber-border-subtle',
  'text-cyber-neon-orange border-cyber-neon-orange/60 bg-cyber-neon-orange/10',
  'text-cyber-neon-pink border-cyber-neon-pink/60 bg-cyber-neon-pink/10',
]

interface Draft {
  level: string
  signals: Record<string, number>
  memo: string
}

function emptyDraft(): Draft {
  const signals: Record<string, number> = {}
  for (const signal of SIGNALS) signals[signal.key] = 0
  return { level: 'normal', signals, memo: '' }
}

const DRAFTS_KEY = 'softad-label-drafts-v1'

function loadDrafts(): Record<string, Draft> {
  try {
    const raw = localStorage.getItem(DRAFTS_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : null
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, Draft>
    }
  } catch {
    // localStorage 不可用或内容损坏时从空草稿开始
  }
  return {}
}

function scoreOf(signals: Record<string, number>): number {
  const total = SIGNALS.reduce((sum, s) => sum + s.weight, 0) * 2
  if (total <= 0) return 0
  const earned = SIGNALS.reduce((sum, s) => sum + s.weight * (signals[s.key] ?? 0), 0)
  return Math.round((earned / total) * 100)
}

function levelStyle(level: string): string {
  return LEVELS.find((item) => item.value === level)?.className ?? ''
}

function levelLabel(level: string): string {
  return LEVELS.find((item) => item.value === level)?.label ?? level
}

export function LabelWorkbench() {
  const [posts, setPosts] = useState<LabelPost[]>([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState<LabelStats | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, Draft>>(loadDrafts)
  const [imgIndex, setImgIndex] = useState(0)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [onlyUnlabeled, setOnlyUnlabeled] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)

  // 草稿持久化：切换视图/刷新页面不丢未保存的标注
  useEffect(() => {
    try {
      localStorage.setItem(DRAFTS_KEY, JSON.stringify(drafts))
    } catch {
      // localStorage 不可用/超配额时静默降级，草稿仍在内存里
    }
  }, [drafts])

  const refreshStats = useCallback(async () => {
    try {
      const s = await labelApi.getStats()
      setStats(s.data)
    } catch {
      // 统计是旁路信息，失败不影响标注
    }
  }, [])

  const load = useCallback(async (): Promise<LabelPost[]> => {
    setLoading(true)
    setError(null)
    let loaded: LabelPost[] = []
    try {
      const res = await labelApi.getAllPosts('xhs', onlyUnlabeled)
      loaded = res.posts
      setPosts(res.posts)
      setTotal(res.total)
      setActiveId((prev) =>
        prev && res.posts.some((post) => post.note_id === prev)
          ? prev
          : res.posts[0]?.note_id ?? null
      )
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
      void refreshStats()
    }
    return loaded
  }, [onlyUnlabeled, refreshStats])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    setImgIndex(0)
  }, [activeId])

  useEffect(() => {
    if (!activeId) return
    const node = listRef.current?.querySelector<HTMLElement>(
      `[data-note-id="${activeId}"]`
    )
    node?.scrollIntoView({ block: 'nearest' })
  }, [activeId])

  const active = useMemo(
    () => posts.find((post) => post.note_id === activeId) ?? null,
    [posts, activeId]
  )

  const activeIndex = useMemo(
    () => posts.findIndex((post) => post.note_id === activeId),
    [posts, activeId]
  )

  const draft: Draft = useMemo(() => {
    if (!active) return emptyDraft()
    const stored = drafts[active.note_id]
    if (stored) return stored
    if (active.verdict) {
      return {
        level: active.verdict.level,
        signals: { ...emptyDraft().signals, ...active.verdict.signals },
        memo: active.verdict.memo,
      }
    }
    return emptyDraft()
  }, [active, drafts])

  const patchDraft = (partial: Partial<Draft>) => {
    if (!active) return
    const next: Draft = { ...draft, ...partial }
    setDrafts((prev) => ({ ...prev, [active.note_id]: next }))
  }

  const save = async (advance: boolean) => {
    if (!active || saving) return
    const savedNoteId = active.note_id
    setSaving(true)
    try {
      const res = await labelApi.saveVerdict({
        note_id: savedNoteId,
        level: draft.level,
        signals: draft.signals,
        memo: draft.memo,
      })
      const verdict = res.data.verdict
      setPosts((prev) =>
        prev.map((post) =>
          post.note_id === savedNoteId ? { ...post, verdict } : post
        )
      )
      setDrafts((prev) => ({
        ...prev,
        [savedNoteId]: {
          level: verdict.level,
          signals: verdict.signals,
          memo: verdict.memo,
        },
      }))
      toast.success('已保存')
      void refreshStats()

      if (advance) {
        const nextId = posts[activeIndex + 1]?.note_id
        if (onlyUnlabeled) {
          const refreshed = await load()
          if (nextId && refreshed.some((post) => post.note_id === nextId)) {
            setActiveId(nextId)
          }
        } else if (nextId) {
          // 只在用户还停留在刚保存的这条时才前进，避免覆盖保存期间的手动选择
          setActiveId((prev) => (prev === savedNoteId ? nextId : prev))
        }
      }
    } catch (error) {
      toast.error('保存失败：' + (error as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const step = (delta: number) => {
    const next = posts[activeIndex + delta]
    if (next) setActiveId(next.note_id)
  }

  const draftScore = scoreOf(draft.signals)
  const images = active?.images ?? []
  const safeImgIndex = images.length > 0 ? Math.min(imgIndex, images.length - 1) : 0

  return (
    <div className="flex flex-1 min-h-0 gap-4 relative z-10">
      {/* 左侧：帖子列表 */}
      <aside className="w-64 flex-shrink-0 glass-panel border border-cyber-border-subtle rounded-lg flex flex-col overflow-hidden">
        <div className="px-3 py-2 border-b border-cyber-border-subtle flex items-center justify-between">
          <div className="flex flex-col">
            <span className="text-xs font-mono text-cyber-text-secondary">已标 / 总数</span>
            <span className="text-sm font-mono text-cyber-text-primary">
              {stats ? `${stats.labeled} / ${stats.total}` : `${posts.length} / ${total}`}
            </span>
          </div>
          <button
            onClick={() => void load()}
            className="p-1.5 rounded-md border border-cyber-border-subtle hover:border-cyber-neon-cyan transition-colors"
            title="刷新"
          >
            <RefreshCw className={`w-3.5 h-3.5 text-cyber-text-secondary ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        <button
          onClick={() => setOnlyUnlabeled((value) => !value)}
          className={`mx-3 mt-2 rounded-md border py-1 text-[11px] font-mono transition-colors ${
            onlyUnlabeled
              ? 'border-cyber-neon-cyan/60 bg-cyber-neon-cyan/10 text-cyber-neon-cyan'
              : 'border-cyber-border-subtle text-cyber-text-muted hover:text-cyber-text-primary'
          }`}
        >
          {onlyUnlabeled ? '只看未标 ✓' : '只看未标'}
        </button>

        <div ref={listRef} className="flex-1 overflow-y-auto">
          {error ? (
            <div className="space-y-2 p-3 text-xs font-mono text-cyber-neon-pink">
              <p className="break-words">加载失败：{error}</p>
              <button
                onClick={() => void load()}
                className="rounded-md border border-cyber-neon-pink/50 px-2 py-1 text-cyber-neon-pink hover:bg-cyber-neon-pink/10"
              >
                重试
              </button>
            </div>
          ) : (
            posts.length === 0 && (
              <p className="p-3 text-xs font-mono text-cyber-text-muted">
                {loading ? '加载中…' : '暂无数据，先用采集页跑一批笔记'}
              </p>
            )
          )}
          {posts.map((post) => {
            const isActive = post.note_id === activeId
            const verdict = post.verdict
            const savedDraft = drafts[post.note_id]
            const level = verdict?.level ?? savedDraft?.level
            return (
              <button
                key={post.note_id}
                data-note-id={post.note_id}
                onClick={() => setActiveId(post.note_id)}
                className={`w-full text-left px-3 py-2 border-b border-cyber-border-subtle/50 transition-colors ${
                  isActive ? 'bg-cyber-neon-cyan/10' : 'hover:bg-cyber-bg-tertiary'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                      level ? 'bg-cyber-neon-cyan' : 'bg-cyber-text-muted/40'
                    }`}
                  />
                  <span className="text-xs font-mono text-cyber-text-secondary truncate">
                    {post.nickname || post.author.slice(0, 8)}
                  </span>
                  {!post.has_media && (
                    <span className="ml-auto text-[10px] font-mono text-cyber-text-muted/70 flex-shrink-0">
                      无图
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-cyber-text-primary line-clamp-2 leading-snug">
                  {post.title || post.desc.slice(0, 40) || '（无标题）'}
                </p>
              </button>
            )
          })}
        </div>
      </aside>

      {/* 中间：小红书视图 */}
      <section className="flex-1 min-w-0 overflow-y-auto flex flex-col items-center py-2">
        {!active && !loading && (
          <p className="text-sm font-mono text-cyber-text-muted mt-10">没有可标注的帖子</p>
        )}

        {active && (
          <>
            <div className="w-full max-w-[460px] flex items-center justify-between mb-2 px-1">
              <button
                onClick={() => step(-1)}
                disabled={activeIndex <= 0}
                className="flex items-center gap-1 text-xs font-mono text-cyber-text-secondary disabled:opacity-30 hover:text-cyber-neon-cyan"
              >
                <ChevronLeft className="w-3.5 h-3.5" /> 上一条
              </button>
              {active.note_url ? (
                <a
                  href={active.note_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 text-xs font-mono text-cyber-text-muted hover:text-cyber-neon-cyan"
                >
                  原帖 <ExternalLink className="w-3 h-3" />
                </a>
              ) : (
                <span className="text-xs font-mono text-cyber-text-muted">无原帖链接</span>
              )}
              <button
                onClick={() => step(1)}
                disabled={activeIndex >= posts.length - 1}
                className="flex items-center gap-1 text-xs font-mono text-cyber-text-secondary disabled:opacity-30 hover:text-cyber-neon-cyan"
              >
                下一条 <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>

            <article className="w-full max-w-[460px] bg-white text-neutral-900 rounded-2xl overflow-hidden shadow-2xl">
              {/* 图片 */}
              <div className="relative aspect-[3/4] bg-neutral-100">
                {images.length > 0 ? (
                  <img
                    src={images[safeImgIndex]}
                    alt=""
                    referrerPolicy="no-referrer"
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <div className="w-full h-full flex flex-col items-center justify-center gap-1 text-neutral-400">
                    <span className="text-sm">无本地图片</span>
                    <span className="text-[11px]">采集时开启 --media 才有图</span>
                  </div>
                )}
                {images.length > 1 && (
                  <>
                    <button
                      onClick={() => setImgIndex((i) => (i - 1 + images.length) % images.length)}
                      className="absolute left-2 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full bg-black/40 text-white flex items-center justify-center"
                    >
                      <ChevronLeft className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => setImgIndex((i) => (i + 1) % images.length)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full bg-black/40 text-white flex items-center justify-center"
                    >
                      <ChevronRight className="w-4 h-4" />
                    </button>
                    <div className="absolute bottom-2 left-1/2 -translate-x-1/2 text-[11px] text-white bg-black/40 px-2 py-0.5 rounded-full">
                      {safeImgIndex + 1} / {images.length}
                    </div>
                  </>
                )}
              </div>

              {/* 正文 */}
              <div className="p-4 space-y-3">
                <h1 className="text-[17px] font-semibold leading-snug">
                  {active.title || '（无标题）'}
                </h1>

                {active.desc && (
                  <p className="text-[14px] leading-relaxed whitespace-pre-wrap text-neutral-700">
                    {active.desc}
                  </p>
                )}

                {active.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {active.tags.map((tag, index) => (
                      <span key={`${tag}-${index}`} className="text-[12px] text-[#13386c]">
                        #{tag}
                      </span>
                    ))}
                  </div>
                )}

                {/* 作者 */}
                <div className="flex items-center gap-2 pt-2 border-t border-neutral-100">
                  <div className="w-8 h-8 rounded-full bg-neutral-200 flex items-center justify-center text-xs font-medium text-neutral-600">
                    {(active.nickname || '?').slice(0, 1)}
                  </div>
                  <div className="flex flex-col leading-tight">
                    <span className="text-[13px] text-neutral-800">{active.nickname || '未知作者'}</span>
                    <span className="text-[11px] text-neutral-400 font-mono">{active.author}</span>
                  </div>
                  <span className="ml-auto text-[11px] text-neutral-400">
                    {active.published_at?.slice(0, 10)}
                  </span>
                </div>

                {/* 互动 */}
                <div className="flex items-center gap-5 text-[12px] text-neutral-500 pt-1">
                  <span className="flex items-center gap-1">
                    <Heart className="w-3.5 h-3.5" /> {active.liked}
                  </span>
                  <span className="flex items-center gap-1">
                    <Star className="w-3.5 h-3.5" /> {active.collected}
                  </span>
                  <span className="flex items-center gap-1">
                    <MessageCircle className="w-3.5 h-3.5" /> {active.comments_count}
                  </span>
                  <span className="flex items-center gap-1">
                    <Share2 className="w-3.5 h-3.5" /> {active.share}
                  </span>
                </div>

                {/* 前排评论 */}
                {active.comments.length > 0 && (
                  <div className="pt-2 border-t border-neutral-100 space-y-2 max-h-64 overflow-y-auto">
                    <p className="text-[12px] text-neutral-400">
                      前排评论（{active.comments.length}）
                    </p>
                    {active.comments.map((comment, index) => (
                      <div key={`${comment.author}-${index}`} className="flex gap-2">
                        <div className="w-6 h-6 rounded-full bg-neutral-200 flex-shrink-0 flex items-center justify-center text-[10px] text-neutral-600">
                          {(comment.nickname || '?').slice(0, 1)}
                        </div>
                        <div className="min-w-0">
                          <p className="text-[11px] text-neutral-500">
                            {comment.nickname || comment.author}
                            <span className="ml-2 text-neutral-300 font-mono">
                              {comment.author}
                            </span>
                          </p>
                          <p className="text-[13px] text-neutral-800 break-words">{comment.content}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </article>
          </>
        )}
      </section>

      {/* 右侧：判据面板 */}
      <aside className="w-80 flex-shrink-0 glass-panel border border-cyber-border-subtle rounded-lg flex flex-col overflow-hidden">
        <div className="px-3 py-2 border-b border-cyber-border-subtle flex items-center justify-between">
          <span className="text-xs font-mono text-cyber-text-secondary">判据打标</span>
          <span className="text-sm font-mono text-cyber-neon-cyan">{draftScore}<span className="text-[10px] text-cyber-text-muted">/100</span></span>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-3">
          {/* 结论 */}
          <div>
            <p className="text-[11px] font-mono text-cyber-text-muted mb-1.5">结论</p>
            <div className="grid grid-cols-3 gap-1.5">
              {LEVELS.map((level) => {
                const selected = draft.level === level.value
                return (
                  <button
                    key={level.value}
                    onClick={() => patchDraft({ level: level.value })}
                    className={`py-1.5 rounded-md border text-xs font-mono transition-colors ${
                      selected ? level.className : 'text-cyber-text-secondary border-cyber-border-subtle'
                    }`}
                  >
                    {level.label}
                  </button>
                )
              })}
            </div>
          </div>

          {/* 五条判据 */}
          {SIGNALS.map((signal) => (
            <div key={signal.key} className="border border-cyber-border-subtle rounded-md p-2">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-xs text-cyber-text-primary">{signal.label}</span>
                <span className="text-[10px] font-mono text-cyber-text-muted">w{signal.weight}</span>
              </div>
              <p className="mt-1 text-[10px] leading-relaxed text-cyber-text-muted">{signal.hint}</p>
              <div className="mt-1.5 grid grid-cols-3 gap-1">
                {STATE_LABELS.map((label, value) => {
                  const selected = (draft.signals[signal.key] ?? 0) === value
                  return (
                    <button
                      key={label}
                      onClick={() =>
                        patchDraft({
                          signals: { ...draft.signals, [signal.key]: value },
                        })
                      }
                      className={`py-1 rounded border text-[11px] font-mono transition-colors ${
                        selected ? STATE_STYLES[value] : 'text-cyber-text-muted border-cyber-border-subtle'
                      }`}
                    >
                      {label}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}

          {/* 反证 / 备注 */}
          <div>
            <p className="text-[11px] font-mono text-cyber-text-muted mb-1.5">
              反证 / 备注 <span className="text-cyber-text-muted/60">（有正常解释就写下来）</span>
            </p>
            <textarea
              value={draft.memo}
              onChange={(event) => patchDraft({ memo: event.target.value })}
              rows={4}
              placeholder="例：多人聚餐，8 人 20 道菜属正常"
              className="w-full resize-none rounded-md bg-cyber-bg-tertiary border border-cyber-border-subtle px-2 py-1.5 text-xs text-cyber-text-primary placeholder:text-cyber-text-muted/50 focus:outline-none focus:border-cyber-neon-cyan"
            />
          </div>

          {active?.verdict && (
            <p className="text-[10px] font-mono text-cyber-text-muted">
              上次保存：{active.verdict.saved_at.slice(0, 19).replace('T', ' ')}
              <span className={`ml-2 ${levelStyle(active.verdict.level)}`}>
                {levelLabel(active.verdict.level)}
              </span>
            </p>
          )}
        </div>

        <div className="p-3 border-t border-cyber-border-subtle flex gap-2">
          <button
            onClick={() => void save(false)}
            disabled={!active || saving}
            className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-md border border-cyber-neon-cyan/60 text-cyber-neon-cyan text-xs font-mono hover:bg-cyber-neon-cyan/10 disabled:opacity-40 transition-colors"
          >
            <Save className="w-3.5 h-3.5" /> 保存
          </button>
          <button
            onClick={() => void save(true)}
            disabled={!active || saving}
            className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-md bg-cyber-neon-cyan/20 border border-cyber-neon-cyan/40 text-cyber-neon-cyan text-xs font-mono hover:bg-cyber-neon-cyan/30 disabled:opacity-40 transition-colors"
          >
            保存并下一条
          </button>
        </div>
      </aside>
    </div>
  )
}
