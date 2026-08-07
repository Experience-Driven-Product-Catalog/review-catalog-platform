import { useCallback, useEffect, useRef, useState } from 'react'
import { MarkdownView } from '../components/MarkdownView'
import { api, Product } from '../lib/api'

export function CatalogPage() {
  const [products, setProducts] = useState<Product[]>([])
  const [selected, setSelected] = useState('')
  const [report, setReport] = useState('')
  const [error, setError] = useState('')
  const [refreshError, setRefreshError] = useState('')
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const requestSequence = useRef(0)

  const loadReport = useCallback(async (productId: string, preserveExisting = false) => {
    const requestId = ++requestSequence.current
    setError('')
    setRefreshError('')

    if (preserveExisting) {
      setRefreshing(true)
      setLoading(false)
    } else {
      setRefreshing(false)
      setLoading(true)
      setReport('')
    }

    try {
      const nextReport = await api.productReport(productId)
      if (requestId !== requestSequence.current) return

      setReport((current) => current === nextReport ? current : nextReport)
    } catch (reason) {
      if (requestId !== requestSequence.current) return

      if (preserveExisting) {
        setRefreshError(`보고서 갱신 여부를 확인하지 못했습니다. 기존 보고서를 유지합니다. (${String(reason)})`)
      } else {
        setError(String(reason))
      }
    } finally {
      if (requestId === requestSequence.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [])

  useEffect(() => {
    api.products().then((items) => {
      setProducts(items)
      setSelected((current) => current || items[0]?.product_id || '')
    }).catch((reason) => setError(String(reason)))
  }, [])

  useEffect(() => {
    if (!selected) return
    void loadReport(selected)
  }, [loadReport, selected])

  return (
    <main className="page catalog-layout">
      <aside className="product-rail">
        <p className="eyebrow">CATALOG INDEX</p>
        <h1>상품 평가 보고서</h1>
        <p className="muted">현재 release에 포함된 정적 보고서를 선택하세요.</p>
        <div className="product-list">
          {products.map((product) => (
            <button
              key={product.product_id}
              className={selected === product.product_id ? 'selected' : ''}
              onClick={() => {
                if (selected === product.product_id) {
                  void loadReport(product.product_id, Boolean(report))
                  return
                }

                requestSequence.current += 1
                setRefreshError('')
                setError('')
                setReport('')
                setSelected(product.product_id)
              }}
            >
              <span>{product.product_name}</span>
              <small>{product.category} · 리뷰 {product.review_count}</small>
            </button>
          ))}
        </div>
      </aside>
      <section className="report-panel" aria-busy={loading || refreshing}>
        {refreshing && <div className="report-refresh-indicator" role="status"><span />갱신 여부 확인 중…</div>}
        {refreshError && <div className="report-refresh-error" role="alert">{refreshError}</div>}
        {error ? <div className="error-card">{error}</div> : report ? <MarkdownView markdown={report} /> : <div className="skeleton">보고서를 불러오는 중…</div>}
      </section>
    </main>
  )
}
