import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Types
export interface CrawlerConfig {
  platform: string
  login_type: string
  crawler_type: string
  keywords: string
  start_page: number
  enable_comments: boolean
  enable_sub_comments: boolean
  enable_media: boolean
  save_option: string
  cookies: string
  headless: boolean
}

export interface CrawlerStatus {
  status: 'idle' | 'running' | 'stopping' | 'error'
  platform: string | null
  crawler_type: string | null
  started_at: string | null
  error_message: string | null
}

export interface LogEntry {
  id: number
  timestamp: string
  level: 'info' | 'warning' | 'error' | 'success' | 'debug'
  message: string
}

export interface DataFile {
  name: string
  path: string
  size: number
  modified_at: number
  record_count: number | null
  type: string
}

export interface FilePreviewResponse {
  data: Record<string, unknown>[]
  total: number
  columns?: string[]
}

export interface Platform {
  value: string
  label: string
  icon: string
}

export interface ConfigOption {
  value: string
  label: string
}

// API functions
export const crawlerApi = {
  start: (config: CrawlerConfig) => api.post('/crawler/start', config),
  stop: () => api.post('/crawler/stop'),
  getStatus: () => api.get<CrawlerStatus>('/crawler/status'),
  getLogs: (limit = 100) => api.get<{ logs: LogEntry[] }>('/crawler/logs', { params: { limit } }),
}

export const dataApi = {
  getFiles: (platform?: string, fileType?: string) =>
    api.get<{ files: DataFile[] }>('/data/files', { params: { platform, file_type: fileType } }),
  getFileContent: (path: string, limit = 100) =>
    api.get<FilePreviewResponse>('/data/files/' + path, { params: { preview: true, limit } }),
  getStats: () => api.get('/data/stats'),
  getDownloadUrl: (path: string) => `/api/data/download/${path}`,
}

export const configApi = {
  getPlatforms: () => api.get<{ platforms: Platform[] }>('/config/platforms'),
  getOptions: () =>
    api.get<{
      login_types: ConfigOption[]
      crawler_types: ConfigOption[]
      save_options: ConfigOption[]
    }>('/config/options'),
}

export interface EnvCheckResult {
  success: boolean
  message: string
  output?: string
  error?: string
}

export const envApi = {
  check: () => api.get<EnvCheckResult>('/env/check'),
}

export interface LabelComment {
  author: string
  nickname: string
  content: string
  likes: number
  at: string | null
}

export interface LabelVerdict {
  note_id: string
  level: string
  signals: Record<string, number>
  memo: string
  saved_at: string
  score: number
}

export interface LabelPost {
  note_id: string
  type: string
  title: string
  desc: string
  tags: string[]
  author: string
  nickname: string
  published_at: string | null
  updated_at: string | null
  liked: number
  collected: number
  comments_count: number
  share: number
  images: string[]
  has_media: boolean
  note_url: string
  source_keyword: string
  comments: LabelComment[]
  verdict: LabelVerdict | null
}

export interface LabelStats {
  platform: string
  total: number
  labeled: number
  unlabeled: number
  levels: Record<string, number>
  verdict_file: string
}

export interface GraphNode {
  id: string
  kind: 'note' | 'author' | 'peer' | 'user' | 'pnote' | 'user2'
  label: string
  sub: string
  r: number
  hop?: number
  center?: boolean
}

export interface GraphEdge {
  source: string
  target: string
  kind: 'comment' | 'own' | 'sim' | 'comment2' | 'back'
  weight: number
}

export interface GraphData {
  center: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  depth?: number
  stats: {
    commenters: number
    peers: number
    peer_ratio: number
    shared_commenters?: number
    back_edges?: number
    hop3_users?: number
    nodes: number
    edges: number
    sim_edges: number
  }
}

export const labelApi = {
  getPosts: (params?: {
    platform?: string
    limit?: number
    offset?: number
    only_unlabeled?: boolean
  }) =>
    api.get<{ posts: LabelPost[]; total: number; platform: string; message?: string }>(
      '/label/posts',
      { params }
    ),
  /** 分页拉取全部帖子，避免界面只看得到固定条数（如默认 200 条）。 */
  getAllPosts: async (platform = 'xhs', onlyUnlabeled = false) => {
    const pageSize = 500
    const posts: LabelPost[] = []
    let total = 0
    for (;;) {
      const res = await api.get<{
        posts: LabelPost[]
        total: number
        platform: string
        message?: string
      }>('/label/posts', {
        params: {
          platform,
          limit: pageSize,
          offset: posts.length,
          only_unlabeled: onlyUnlabeled,
        },
      })
      total = res.data.total
      if (res.data.posts.length === 0) break
      posts.push(...res.data.posts)
      if (posts.length >= total) break
    }
    return { posts, total, platform }
  },
  saveVerdict: (payload: {
    note_id: string
    level: string
    signals: Record<string, number>
    memo: string
  }) => api.post<{ ok: boolean; verdict: LabelVerdict }>('/label/verdict', payload),
  getStats: (platform = 'xhs') =>
    api.get<LabelStats>('/label/stats', { params: { platform } }),
  getGraph: (noteId?: string, platform = 'xhs', depth = 2) =>
    api.get<GraphData>('/label/graph', {
      params: { note_id: noteId || undefined, platform, depth },
    }),
}

export default api
