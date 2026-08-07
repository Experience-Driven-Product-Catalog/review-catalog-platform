import { useEffect, useState } from 'react'
import { MarkdownView } from '../components/MarkdownView'
import { api, Product } from '../lib/api'

export function CatalogPage() {
  const [products, setProducts] = useState<Product[]>([])
  const [selected, setSelected] = useState('')
  const [report, setReport] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api.products().then((items) => {
      setProducts(items)
      setSelected((current) => current || items[0]?.product_id || '')
    }).catch((reason) => setError(String(reason)))
  }, [])

  useEffect(() => {
    if (!selected) return
    api.productReport(selected).then(setReport).catch((reason) => setError(String(reason)))
  }, [selected])

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
      <section className="report-panel">
        {error ? <div className="error-card">{error}</div> : report ? <MarkdownView markdown={report} /> : <div className="skeleton">보고서를 불러오는 중…</div>}
      </section>
    </main>
  )
}
