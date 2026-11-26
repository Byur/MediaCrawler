import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { RefreshCw } from 'lucide-react'
import { labelApi, type GraphData, type GraphNode, type LabelPost } from '@/lib/api'

const NODE_COLOR: Record<string, string> = {
  note: '#22d3ee',
  author: '#f472b6',
  peer: '#fbbf24',
  user: '#94a3b8',
  pnote: '#64748b',
  user2: '#a78bfa',
}

const EDGE_COLOR: Record<string, string> = {
  comment: 'rgba(34, 211, 238, 0.55)',
  own: 'rgba(244, 114, 182, 0.28)',
  sim: 'rgba(251, 191, 36, 0.50)',
  comment2: 'rgba(167, 139, 250, 0.45)',
  back: 'rgba(244, 63, 94, 0.85)',
}

interface SimNode extends GraphNode {
  x: number
  y: number
  vx: number
  vy: number
  hop: number
  fixed: boolean
}

function hopOf(node: GraphNode): number {
  if (typeof node.hop === 'number') return node.hop
  if (node.kind === 'note') return 0
  if (node.kind === 'pnote') return 2
  if (node.kind === 'user2') return 3
  return 1
}

/** 用节点 id 生成稳定的初始角度，避免每次 resize 重排后图完全换位置。 */
function seedAngle(id: string): number {
  let hash = 0
  for (let i = 0; i < id.length; i += 1) {
    hash = (hash * 31 + id.charCodeAt(i)) | 0
  }
  return (((hash >>> 0) % 3600) / 3600) * Math.PI * 2
}

/** 中心固定 + 径向分层 + 弹簧 + 斥力；返回屏幕坐标。 */
function layout(data: GraphData, width: number, height: number): Map<string, SimNode> {
  const radius = Math.max(120, Math.min(width, height) / 2 - 70)
  const ringRadius = [0, radius * 0.38, radius * 0.66, radius * 0.94]
  const nodes: SimNode[] = data.nodes.map((node) => {
    const hop = Math.min(3, Math.max(0, hopOf(node)))
    const angle = seedAngle(node.id)
    const r = ringRadius[hop]
    return {
      ...node,
      x: Math.cos(angle) * r,
      y: Math.sin(angle) * r,
      vx: 0,
      vy: 0,
      hop,
      fixed: hop === 0,
    }
  })
  const index = new Map(nodes.map((n) => [n.id, n]))

  for (let step = 0; step < 420; step += 1) {
    // 弹簧
    for (const edge of data.edges) {
      const a = index.get(edge.source)
      const b = index.get(edge.target)
      if (!a || !b) continue
      const dx = b.x - a.x
      const dy = b.y - a.y
      const dist = Math.hypot(dx, dy) || 1
      const rest = edge.kind === 'sim' ? radius * 0.34 : radius * 0.20
      const force = (dist - rest) * 0.02 * (edge.weight || 1)
      const fx = (dx / dist) * force
      const fy = (dy / dist) * force
      a.vx += fx
      a.vy += fy
      b.vx -= fx
      b.vy -= fy
    }
    // 斥力
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const a = nodes[i]
        const b = nodes[j]
        const dx = a.x - b.x
        const dy = a.y - b.y
        const dist = Math.hypot(dx, dy) || 1
        const force = 900 / (dist * dist)
        const fx = (dx / dist) * force
        const fy = (dy / dist) * force
        a.vx += fx
        a.vy += fy
        b.vx -= fx
        b.vy -= fy
      }
    }
    // 径向约束
    for (const node of nodes) {
      if (node.fixed) {
        node.x = 0
        node.y = 0
        node.vx = 0
        node.vy = 0
        continue
      }
      const dist = Math.hypot(node.x, node.y) || 1
      const pull = (ringRadius[node.hop] - dist) * 0.03
      node.vx += (node.x / dist) * pull
      node.vy += (node.y / dist) * pull
      node.x += node.vx
      node.y += node.vy
      node.vx *= 0.35
      node.vy *= 0.35
    }
  }

  const result = new Map<string, SimNode>()
  const cx = width / 2
  const cy = height / 2
  for (const node of nodes) {
    result.set(node.id, { ...node, x: cx + node.x, y: cy + node.y })
  }
  return result
}

export function GraphView() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [posts, setPosts] = useState<LabelPost[]>([])
  const [centerId, setCenterId] = useState<string>('')
  const [depth, setDepth] = useState<2 | 3>(2)
  const [data, setData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(false)
  const [size, setSize] = useState({ width: 800, height: 600 })
  const [selected, setSelected] = useState<GraphNode | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const requestSeq = useRef(0)

  useEffect(() => {
    labelApi
      .getAllPosts('xhs')
      .then((res) => {
        setPosts(res.posts)
        if (!centerId && res.posts.length > 0) setCenterId(res.posts[0].note_id)
      })
      .catch((error) => toast.error('加载笔记列表失败：' + (error as Error).message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const load = useCallback(async (noteId: string, d: 2 | 3) => {
    if (!noteId) return
    const seq = requestSeq.current + 1
    requestSeq.current = seq
    setLoading(true)
    setError(null)
    setData(null)
    try {
      const res = await labelApi.getGraph(noteId, 'xhs', d)
      if (requestSeq.current !== seq) return
      setData(res.data)
      setSelected(null)
    } catch (err) {
      if (requestSeq.current !== seq) return
      setError((err as Error).message)
      toast.error('加载关系图失败：' + (err as Error).message)
    } finally {
      if (requestSeq.current === seq) setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (centerId) void load(centerId, depth)
  }, [centerId, depth, load])

  useEffect(() => {
    const element = wrapRef.current
    if (!element) return
    const observer = new ResizeObserver(() => {
      setSize({ width: element.clientWidth, height: element.clientHeight })
    })
    observer.observe(element)
    setSize({ width: element.clientWidth, height: element.clientHeight })
    return () => observer.disconnect()
  }, [])

  const positioned = useMemo(() => {
    if (!data) return new Map<string, SimNode>()
    return layout(data, size.width, size.height)
  }, [data, size])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !data) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const isDark = document.documentElement.classList.contains('dark')
    const dpr = window.devicePixelRatio || 1
    canvas.width = size.width * dpr
    canvas.height = size.height * dpr
    canvas.style.width = `${size.width}px`
    canvas.style.height = `${size.height}px`
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, size.width, size.height)

    // 边
    for (const edge of data.edges) {
      const a = positioned.get(edge.source)
      const b = positioned.get(edge.target)
      if (!a || !b) continue
      const active = hover === edge.source || hover === edge.target
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(b.x, b.y)
      ctx.strokeStyle = EDGE_COLOR[edge.kind] ?? 'rgba(148,163,184,0.3)'
      ctx.lineWidth = active ? 2 : edge.kind === 'sim' ? 1 : 1.2
      ctx.setLineDash(edge.kind === 'sim' ? [4, 4] : [])
      ctx.globalAlpha = active ? 1 : 0.85
      ctx.stroke()
    }
    ctx.setLineDash([])
    ctx.globalAlpha = 1

    // 节点
    for (const node of data.nodes) {
      const p = positioned.get(node.id)
      if (!p) continue
      const color = NODE_COLOR[node.kind] ?? '#94a3b8'
      const radius = node.kind === 'note' ? 10 : node.kind === 'author' ? 7.5 : node.kind === 'peer' ? 6 : node.kind === 'pnote' ? 3.2 : 4.5
      ctx.beginPath()
      ctx.arc(p.x, p.y, radius, 0, Math.PI * 2)
      ctx.fillStyle = color
      ctx.globalAlpha = node.kind === 'pnote' ? 0.55 : 0.95
      ctx.fill()
      if (node.center || hover === node.id || selected?.id === node.id) {
        ctx.beginPath()
        ctx.arc(p.x, p.y, radius + 4, 0, Math.PI * 2)
        ctx.strokeStyle = color
        ctx.lineWidth = 1.5
        ctx.globalAlpha = 0.9
        ctx.stroke()
      }
      ctx.globalAlpha = 1

      if (node.kind === 'note' || node.kind === 'author' || node.kind === 'peer') {
        const label = node.label.length > 12 ? `${node.label.slice(0, 12)}…` : node.label
        const below = node.kind !== 'author'
        const ty = below ? p.y + radius + 15 : p.y - radius - 9
        ctx.font = node.kind === 'note' ? '600 12px ui-sans-serif, system-ui' : '11px ui-monospace, monospace'
        ctx.textAlign = 'center'
        // 描边做底，明暗主题都能读
        ctx.lineWidth = 3
        ctx.strokeStyle = isDark ? 'rgba(11,16,32,0.85)' : 'rgba(255,255,255,0.9)'
        ctx.strokeText(label, p.x, ty)
        ctx.fillStyle = hover === node.id || selected?.id === node.id ? '#22d3ee' : (isDark ? '#e2e8f0' : '#334155')
        ctx.fillText(label, p.x, ty)
      }
    }
  }, [data, positioned, size, hover, selected])

  const pick = (clientX: number, clientY: number): GraphNode | null => {
    const canvas = canvasRef.current
    if (!canvas || !data) return null
    const rect = canvas.getBoundingClientRect()
    const x = clientX - rect.left
    const y = clientY - rect.top
    let best: GraphNode | null = null
    let bestDist = 16
    for (const node of data.nodes) {
      const p = positioned.get(node.id)
      if (!p) continue
      const dist = Math.hypot(p.x - x, p.y - y)
      if (dist < bestDist) {
        bestDist = dist
        best = node
      }
    }
    return best
  }

  const stats = data?.stats

  return (
    <div className="flex flex-1 min-h-0 gap-4 relative z-10">
      <aside className="w-72 flex-shrink-0 glass-panel border border-cyber-border-subtle rounded-lg flex flex-col overflow-hidden">
        <div className="px-3 py-2 border-b border-cyber-border-subtle">
          <p className="text-xs font-mono text-cyber-text-secondary mb-1.5">以哪篇笔记为中心</p>
          <select
            value={centerId}
            onChange={(event) => setCenterId(event.target.value)}
            className="w-full rounded-md bg-cyber-bg-tertiary border border-cyber-border-subtle px-2 py-1.5 text-xs text-cyber-text-primary focus:outline-none focus:border-cyber-neon-cyan"
          >
            {posts.map((post) => (
              <option key={post.note_id} value={post.note_id}>
                {(post.title || post.desc.slice(0, 20) || post.note_id).slice(0, 26)}
              </option>
            ))}
          </select>
          <div className="mt-2 grid grid-cols-2 gap-1.5">
            {([2, 3] as const).map((d) => (
              <button
                key={d}
                onClick={() => setDepth(d)}
                className={`py-1 rounded border text-[11px] font-mono transition-colors ${
                  depth === d
                    ? 'text-cyber-neon-cyan border-cyber-neon-cyan/60 bg-cyber-neon-cyan/10'
                    : 'text-cyber-text-muted border-cyber-border-subtle'
                }`}
              >
                {d} 跳
              </button>
            ))}
          </div>
          <button
            onClick={() => centerId && void load(centerId, depth)}
            className="mt-2 w-full flex items-center justify-center gap-1.5 py-1.5 rounded-md border border-cyber-border-subtle text-xs font-mono text-cyber-text-secondary hover:border-cyber-neon-cyan hover:text-cyber-neon-cyan transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> 重新布局
          </button>
        </div>

        <div className="p-3 grid grid-cols-2 gap-2">
          {[
            ['评论者', stats?.commenters ?? '—'],
            ['同行', stats?.peers ?? '—'],
            ['同行占比', stats ? `${Math.round((stats.peer_ratio ?? 0) * 100)}%` : '—'],
            ['共享评论者', stats?.shared_commenters ?? 0],
            ['回流边', stats?.back_edges ?? 0],
            ['第三跳用户', stats?.hop3_users ?? 0],
            ['节点 / 边', stats ? `${stats.nodes} / ${stats.edges}` : '—'],
          ].map(([label, value]) => (
            <div key={label} className="rounded-md border border-cyber-border-subtle px-2 py-1.5">
              <p className="text-[10px] font-mono text-cyber-text-muted">{label}</p>
              <p className="text-sm font-mono text-cyber-text-primary">{value}</p>
            </div>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto px-3 pb-3">
          {selected ? (
            <div className="rounded-md border border-cyber-border-subtle p-2 space-y-1">
              <p className="text-[10px] font-mono text-cyber-text-muted">选中节点</p>
              <p className="text-xs text-cyber-text-primary break-words">{selected.label}</p>
              <p className="text-[10px] font-mono text-cyber-text-muted">{selected.kind} · {selected.sub}</p>
            </div>
          ) : (
            <p className="text-[11px] text-cyber-text-muted leading-relaxed">
              点击节点查看信息。中心为被研究的笔记，第一圈是它的评论者，
              第二圈是这些评论者自己的内容。虚线表示文案相似。
            </p>
          )}
        </div>
      </aside>

      <section
        ref={wrapRef}
        className="flex-1 min-w-0 glass-panel border border-cyber-border-subtle rounded-lg relative overflow-hidden"
      >
        <canvas
          ref={canvasRef}
          className="absolute inset-0"
          onMouseMove={(event) => setHover(pick(event.clientX, event.clientY)?.id ?? null)}
          onMouseLeave={() => setHover(null)}
          onClick={(event) => setSelected(pick(event.clientX, event.clientY))}
        />
        <div className="absolute left-3 bottom-3 flex flex-wrap gap-3 text-[10px] font-mono text-cyber-text-muted">
          {[
            ['#22d3ee', '笔记 / 中心'],
            ['#f472b6', '作者 / 归属'],
            ['#fbbf24', '同行'],
            ['#94a3b8', '普通用户'],
            ['#a78bfa', '第三跳'],
            ['#64748b', '相关笔记'],
          ].map(([color, label]) => (
            <span key={label} className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full" style={{ background: color }} />
              {label}
            </span>
          ))}
          <span className="text-cyber-text-muted/70">
            虚线 = 文案相似 · 实线 = 评论/归属 · 红线 = 同一人回流
          </span>
        </div>
        {loading && (
          <p className="absolute inset-0 flex items-center justify-center text-xs font-mono text-cyber-text-muted">
            加载中…
          </p>
        )}
        {error && !loading && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-xs font-mono text-cyber-neon-pink">
            <p className="px-6 text-center break-words">加载关系图失败：{error}</p>
            <button
              onClick={() => centerId && void load(centerId, depth)}
              className="rounded-md border border-cyber-neon-pink/50 px-2 py-1 hover:bg-cyber-neon-pink/10"
            >
              重试
            </button>
          </div>
        )}
        {!data && !loading && !error && (
          <p className="absolute inset-0 flex items-center justify-center text-xs font-mono text-cyber-text-muted">
            选择一篇笔记以生成关系图
          </p>
        )}
      </section>
    </div>
  )
}
