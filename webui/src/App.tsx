import { useState } from 'react'
import { Toaster } from 'sonner'
import { Sidebar } from '@/components/layout/Sidebar'
import { MainContent } from '@/components/layout/MainContent'
// 个人 fork：隐藏原作者页脚（组件源码保留在 AuthorFooter.tsx）
// import { AuthorFooter } from '@/components/layout/AuthorFooter'
import { CrawlerConfigPanel } from '@/components/config/CrawlerConfigPanel'
import { LabelWorkbench } from '@/components/label/LabelWorkbench'
import { GraphView } from '@/components/graph/GraphView'
import { EnvironmentCheck, isEnvChecked } from '@/components/env/EnvironmentCheck'

export type AppView = 'crawler' | 'label' | 'graph'

function App() {
  // 个人 fork：已移除使用责任声明弹窗与入口（非商业个人项目）
  // Initialize by checking localStorage if env check has passed
  const [envChecked, setEnvChecked] = useState(() => isEnvChecked())
  // 顶部视图：采集 / 标注 / 图谱
  const [view, setView] = useState<AppView>('crawler')

  const handleEnvCheckComplete = () => {
    setEnvChecked(true)
  }

  return (
    <div className="flex flex-col h-screen cyber-grid overflow-hidden relative">
      {/* Environment Check Modal */}
      {!envChecked && (
        <EnvironmentCheck onCheckComplete={handleEnvCheckComplete} />
      )}

      {/* Header Bar */}
      <Sidebar view={view} onChangeView={setView} />

      {/* Main Area */}
      {view === 'label' ? (
        <div className="flex-1 flex flex-col p-4 overflow-hidden min-h-0">
          <LabelWorkbench />
        </div>
      ) : view === 'graph' ? (
        <div className="flex-1 flex flex-col p-4 overflow-hidden min-h-0">
          <GraphView />
        </div>
      ) : (
        <div className="flex-1 flex flex-col gap-4 p-4 overflow-hidden min-h-0">
          {/* Config Panel - Primary Action Area (Always Expanded) */}
          <div className="flex-shrink-0">
            <CrawlerConfigPanel />
          </div>

          {/* Console - Collapsible Terminal */}
          <MainContent />
        </div>
      )}

      {/* 个人 fork：原作者信息页脚已隐藏，保留源码备查
      <AuthorFooter />
      */}

      {/* Toast notifications - Theme-aware style */}
      <Toaster
        position="top-right"
        toastOptions={{
          className: 'glass-panel font-mono text-cyber-text-primary',
          style: {
            fontFamily: 'JetBrains Mono, monospace',
          },
        }}
      />
    </div>
  )
}

export default App
