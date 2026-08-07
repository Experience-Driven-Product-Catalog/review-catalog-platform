import { useEffect, useState } from 'react'
import { MarkdownView } from '../components/MarkdownView'
import { api } from '../lib/api'

export function HomePage() {
  const [markdown, setMarkdown] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api.about().then((result) => setMarkdown(result.markdown)).catch((reason) => setError(String(reason)))
  }, [])

  return (
    <main className="page home-page">
      <section className="hero">
        <p className="eyebrow">HTTPS · GITHUB ACTIONS · CODEDEPLOY</p>
        <h1>리뷰 근거가<br />배포 가능한 카탈로그가 되는 과정</h1>
        <p>
          추출, 정규화 후보 판정, immutable snapshot과 규칙 기반 보고서를 하나의 release로 묶고
          검증된 변경만 HTTPS 카탈로그에 배포합니다.
        </p>
        <div className="hero-metrics">
          <span><strong>1</strong> DuckDB writer</span>
          <span><strong>3</strong> product views</span>
          <span><strong>0</strong> report-time LLM calls</span>
        </div>
      </section>
      <section className="readme-panel">
        {error ? <div className="error-card">{error}</div> : markdown ? <MarkdownView markdown={markdown} /> : <div className="skeleton">README를 불러오는 중…</div>}
      </section>
    </main>
  )
}
