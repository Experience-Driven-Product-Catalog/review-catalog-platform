import { FormEvent, useEffect, useMemo, useState } from 'react'
import { MarkdownView } from '../components/MarkdownView'
import { api, Product, SubmissionStatus } from '../lib/api'

const terminalStates = new Set(['completed', 'failed', 'dispatch_failed', 'release_failed'])

export function DemoPage() {
  const [products, setProducts] = useState<Product[]>([])
  const [productId, setProductId] = useState('')
  const [reviews, setReviews] = useState([''])
  const [submissionId, setSubmissionId] = useState('')
  const [status, setStatus] = useState<SubmissionStatus | null>(null)
  const [activeResult, setActiveResult] = useState(0)
  const [remainingSeconds, setRemainingSeconds] = useState(0)
  const [timerRunning, setTimerRunning] = useState(false)
  const [error, setError] = useState('')
  const busy = Boolean(submissionId && !status?.state.startsWith('completed') && !terminalStates.has(status?.state ?? ''))

  useEffect(() => {
    api.products().then((items) => {
      setProducts(items)
      setProductId(items[0]?.product_id ?? '')
    }).catch((reason) => setError(String(reason)))
  }, [])

  useEffect(() => {
    if (!submissionId) return
    let cancelled = false
    const poll = async () => {
      try {
        const next = await api.submission(submissionId)
        if (!cancelled) {
          setStatus(next)
          if (terminalStates.has(next.state)) setTimerRunning(false)
        }
        if (!terminalStates.has(next.state) && !cancelled) window.setTimeout(poll, 1800)
      } catch (reason) {
        if (!cancelled) {
          setTimerRunning(false)
          setError(String(reason))
        }
      }
    }
    poll()
    return () => { cancelled = true }
  }, [submissionId])

  useEffect(() => {
    if (!timerRunning || remainingSeconds === 0) return
    const timer = window.setTimeout(() => {
      setRemainingSeconds((seconds) => Math.max(0, seconds - 1))
    }, 1000)
    return () => window.clearTimeout(timer)
  }, [timerRunning, remainingSeconds])

  const progress = useMemo(() => {
    const state = status?.state ?? (submissionId ? 'queued' : 'ready')
    const steps = ['queued', 'running', 'pipeline_succeeded', 'completed']
    return Math.max(0, steps.indexOf(state))
  }, [status, submissionId])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setStatus(null)
    setActiveResult(0)
    const trimmed = reviews.map((review) => review.trim()).filter(Boolean)
    setRemainingSeconds(Math.ceil(trimmed.length / 4) * 60)
    setTimerRunning(true)
    try {
      const accepted = await api.submitReviews(productId, trimmed)
      setSubmissionId(accepted.submission_id)
    } catch (reason) {
      setTimerRunning(false)
      setError(String(reason))
    }
  }

  const result = status?.results[activeResult]
  return (
    <main className="page demo-page">
      <section className="demo-intro">
        <p className="eyebrow">LIVE PIPELINE DEMO</p>
        <h1>한 문장의 경험을<br />검증 가능한 속성으로</h1>
        <p>React는 FastAPI에만 요청합니다. Airflow가 단일 DuckDB writer로 반영하고, 완성된 release만 화면에 나타납니다.</p>
      </section>
      <section className="demo-card">
        <form onSubmit={submit}>
          <label>상품</label>
          <select value={productId} onChange={(event) => setProductId(event.target.value)} disabled={busy}>
            {products.map((product) => <option key={product.product_id} value={product.product_id}>{product.product_name}</option>)}
          </select>
          <div className="review-label-row">
            <label>리뷰</label>
            <button type="button" className="text-button" onClick={() => setReviews([...reviews, ''])} disabled={busy || reviews.length >= 20}>+ 리뷰 추가</button>
          </div>
          {reviews.map((review, index) => (
            <div className="review-input" key={index}>
              <span>{String(index + 1).padStart(2, '0')}</span>
              <textarea
                value={review}
                onChange={(event) => setReviews(reviews.map((item, position) => position === index ? event.target.value : item))}
                placeholder="예: 화질은 선명하지만 화면 밝기가 조금 어두워요."
                disabled={busy}
                required
              />
              {reviews.length > 1 && <button type="button" onClick={() => setReviews(reviews.filter((_, position) => position !== index))}>×</button>}
            </div>
          ))}
          <button className="primary-button" type="submit" disabled={busy || !productId}>속성 추출 및 카탈로그 반영</button>
        </form>
        {submissionId && (
          <div className="pipeline-status">
            <div className="status-head"><span>Pipeline</span><strong>{status?.state ?? 'queued'}</strong></div>
            <div className="progress-row">
              <div className="progress-track"><span style={{ width: `${(progress + 1) * 25}%` }} /></div>
              <div className="pipeline-timer" aria-label={`남은 예상 시간 ${remainingSeconds}초`}>
                <span>Timer</span><strong>{remainingSeconds}s</strong>
              </div>
            </div>
            <div className="steps"><span>접수</span><span>추출·정규화</span><span>release 생성</span><span>완료</span></div>
          </div>
        )}
        {error && <div className="error-card">{error}</div>}
      </section>
      {status?.state === 'completed' && (
        <section className="demo-results">
          <div className="result-tabs">
            {status.results.map((item, index) => <button key={item.demo_review_id} className={activeResult === index ? 'selected' : ''} onClick={() => setActiveResult(index)}>리뷰 {index + 1}</button>)}
            <a href="/catalog">갱신된 상품 보고서 보기 →</a>
          </div>
          {result && (
            result.opinion_units.length === 0 ? (
              <div className="empty-opinion-result">
                <span aria-hidden="true">—</span>
                <p>배송 상태와 같이 상품과 관련되지 않은 속성은 추출하지 않습니다.</p>
              </div>
            ) : (
              <>
                <div className="unit-grid">
                  {result.opinion_units.map((unit, index) => (
                    <article key={index}>
                      <span className={`sentiment ${unit.sentiment}`}>{unit.sentiment}</span>
                      <h3>{unit.aspect ?? unit.raw_aspect} <small>› {unit.status ?? unit.raw_status ?? '상태 없음'}</small></h3>
                      <p>{unit.excerpt}</p>
                      <footer>
                        {unit.mapping_state === 'candidate'
                          ? `미등록 후보 · ${unit.suggested_aspect ?? '최근접 군집 없음'} · canonical d=${unit.aspect_distance ?? '—'} · complete-link max=${unit.aspect_membership_max_distance ?? '—'} · threshold ${unit.aspect_candidate_eligible ? '통과' : '미통과'}`
                          : unit.mapping_state === 'excluded_taxonomy'
                            ? 'taxonomy 집계 제외 규칙 적용'
                            : `mapping table exact match · normalization ${unit.normalization_run_id}`}
                      </footer>
                    </article>
                  ))}
                </div>
                {result.proposal_markdown && <div className="proposal-panel"><MarkdownView markdown={result.proposal_markdown} /></div>}
              </>
            )
          )}
        </section>
      )}
    </main>
  )
}
