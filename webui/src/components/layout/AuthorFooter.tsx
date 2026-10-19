// 个人 fork：原作者信息页脚默认不在 App 中渲染（见 App.tsx 的注释），此处保留源码备查。
//
// 为避免这些信息被打进构建产物，license.json 里的 author.* 文案已移除，原文记录如下：
//   zh-CN: 程序员阿江-Relakkes / 40K+ Star 开源项目作者 /
//          40K开源项目MediaCrawler作者，专注爬虫技术和AI Agent知识分享 / 开源不易，求个关注 ⭐
//   en-US: Relakkes (阿江) / 40K+ Star Open Source Author /
//          Author of MediaCrawler (40K stars), focused on crawler tech and AI Agent knowledge sharing /
//          Open source is hard, please star ⭐
// 原作者社交链接（同样已从页面隐藏）：GitHub github.com/NanmiCoder，B站 space.bilibili.com/434377496，
// 小红书 /user/profile/5f58bd990000000001003753，抖音 /user/MS4wLjABAAAATJPY7LAlaa5X-c8uNdWkvz0jUGgpw4eeXIwu_8BhvqE
// 如需恢复页脚：取消 App.tsx 里的 import / 使用注释，并同步恢复两个 license.json 的 author.* 文案。
import { useTranslation } from 'react-i18next'
import { Sparkles, Heart } from 'lucide-react'

export function AuthorFooter() {
  const { t } = useTranslation('license')

  return (
    <footer className="h-24 flex-shrink-0 glass-panel border-t border-cyber-border-subtle">
      <div className="h-full px-6 flex items-center justify-center gap-6">
        {/* Author Avatar */}
        <div className="w-14 h-14 rounded-lg overflow-hidden border-2 border-cyber-neon-cyan/60 flex-shrink-0 shadow-glow-cyan-sm">
          <img
            src="/logos/my_logo.png"
            alt="程序员阿江-Relakkes"
            className="w-full h-full object-cover"
          />
        </div>

        {/* Author Info */}
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold text-cyber-text-primary">
              {t('author.name')}
            </span>
            <Sparkles className="w-5 h-5 text-cyber-neon-cyan animate-pulse" />
          </div>
          <span className="text-sm text-cyber-text-muted hidden sm:inline">
            {t('author.description')}
          </span>
          <div className="flex items-center gap-2 text-cyber-neon-cyan">
            <Heart className="w-4 h-4 fill-current animate-pulse" />
            <span className="text-sm font-medium">
              {t('author.slogan')}
            </span>
          </div>
        </div>

        {/* Social Links */}
        <div className="flex items-center gap-3">
          <a
            href="https://github.com/NanmiCoder"
            target="_blank"
            rel="noopener noreferrer"
            className="w-11 h-11 rounded-lg flex items-center justify-center border border-cyber-border-subtle hover:border-cyber-neon-cyan hover:shadow-glow-cyan-sm transition-all bg-cyber-bg-tertiary hover:scale-110"
            title="GitHub"
          >
            <img src="/logos/github.png" alt="GitHub" className="w-6 h-6 object-contain" />
          </a>
          <a
            href="https://space.bilibili.com/434377496"
            target="_blank"
            rel="noopener noreferrer"
            className="w-11 h-11 rounded-lg flex items-center justify-center border border-cyber-border-subtle hover:border-pink-400 hover:shadow-[0_0_10px_rgba(251,113,133,0.4)] transition-all bg-cyber-bg-tertiary hover:scale-110"
            title="哔哩哔哩"
          >
            <img src="/logos/bilibili_logo.png" alt="Bilibili" className="w-6 h-6 object-contain" />
          </a>
          <a
            href="https://www.xiaohongshu.com/user/profile/5f58bd990000000001003753"
            target="_blank"
            rel="noopener noreferrer"
            className="w-11 h-11 rounded-lg flex items-center justify-center border border-cyber-border-subtle hover:border-red-400 hover:shadow-[0_0_10px_rgba(248,113,113,0.4)] transition-all bg-cyber-bg-tertiary hover:scale-110"
            title="小红书"
          >
            <img src="/logos/xiaohongshu_logo.png" alt="小红书" className="w-6 h-6 object-contain" />
          </a>
          <a
            href="https://www.douyin.com/user/MS4wLjABAAAATJPY7LAlaa5X-c8uNdWkvz0jUGgpw4eeXIwu_8BhvqE"
            target="_blank"
            rel="noopener noreferrer"
            className="w-11 h-11 rounded-lg flex items-center justify-center border border-cyber-border-subtle hover:border-cyber-text-primary hover:shadow-[0_0_10px_rgba(255,255,255,0.3)] transition-all bg-cyber-bg-tertiary hover:scale-110"
            title="抖音"
          >
            <img src="/logos/douyin.png" alt="抖音" className="w-6 h-6 object-contain" />
          </a>
        </div>
      </div>
    </footer>
  )
}
