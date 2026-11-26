import { Bug, Wifi } from 'lucide-react'
// 个人 fork：隐藏指向原作者仓库的 Star 入口（源码保留）
// import { Github } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { useCrawlerStore } from '@/store/crawlerStore'
import { useCrawlerStatus } from '@/hooks/useCrawler'
import { LanguageSwitch } from './LanguageSwitch'
import { ThemeToggle } from './ThemeToggle'

interface SidebarProps {
  view?: 'crawler' | 'label' | 'graph'
  onChangeView?: (view: 'crawler' | 'label' | 'graph') => void
}

export function Sidebar({ view = 'crawler', onChangeView }: SidebarProps) {
  const { t } = useTranslation()
  const status = useCrawlerStore((state) => state.status)

  // Poll status
  useCrawlerStatus()

  const isRunning = status === 'running'

  return (
    <header className="h-14 flex-shrink-0 glass-panel border-b border-cyber-border-subtle relative z-10">
      <div className="h-full px-4 flex items-center justify-between">
        {/* Left: Logo and GitHub Star */}
        <div className="flex items-center gap-3">
          <Bug className="w-5 h-5 text-cyber-neon-cyan" />
          <span className="font-mono font-bold text-cyber-text-primary tracking-wider text-sm">
            MediaCrawler
          </span>
          {/* 个人 fork：原作者仓库 Star 入口已隐藏，保留源码备查
          <a
            href="https://github.com/NanmiCoder/MediaCrawler"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 px-2 py-1 rounded-md border border-cyber-border-subtle hover:border-cyber-neon-cyan hover:shadow-glow-cyan-sm transition-all bg-cyber-bg-tertiary"
          >
            <Github className="w-4 h-4 text-cyber-text-secondary" />
            <span className="text-xs font-mono text-cyber-text-secondary">Star</span>
          </a>
          */}
          {isRunning && (
            <Badge variant="running" className="text-[10px]">
              {t('status.active')}
            </Badge>
          )}
          {isRunning && (
            <span className="w-2 h-2 bg-cyber-neon-green rounded-full shadow-glow-green-sm animate-pulse-fast" />
          )}

          {/* 视图切换：采集 / 标注 */}
          <div className="ml-2 flex items-center rounded-md border border-cyber-border-subtle overflow-hidden">
            {(['crawler', 'label', 'graph'] as const).map((item) => (
              <button
                key={item}
                onClick={() => onChangeView?.(item)}
                className={`px-2.5 py-1 text-xs font-mono transition-colors ${
                  view === item
                    ? 'bg-cyber-neon-cyan/15 text-cyber-neon-cyan'
                    : 'text-cyber-text-secondary hover:text-cyber-text-primary'
                }`}
              >
                {item === 'crawler' ? '采集' : item === 'label' ? '标注' : '图谱'}
              </button>
            ))}
          </div>
        </div>

        {/* Right: Actions and Status */}
        <div className="flex items-center gap-3">
          {/* Theme Toggle */}
          <ThemeToggle />
          {/* Language Switch */}
          <LanguageSwitch />

          {/* Status Info */}
          <div className="hidden lg:flex items-center gap-2 text-xs font-mono">
            <span className="text-cyber-text-muted">{t('sidebar.api')}:</span>
            <span className="text-cyber-neon-green">v1.0.0</span>
            <div className="flex items-center gap-1.5">
              <Wifi className="w-3 h-3 text-cyber-text-secondary" />
              <span className="text-cyber-text-secondary">{t('sidebar.local')}</span>
              <span className="status-dot status-dot-online" />
            </div>
          </div>
        </div>
      </div>
    </header>
  )
}
