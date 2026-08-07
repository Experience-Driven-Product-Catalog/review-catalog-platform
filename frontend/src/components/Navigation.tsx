const links = [
  ['/', '프로젝트'],
  ['/catalog', '상품 보고서'],
  ['/demo', '리뷰 데모'],
]

export function Navigation() {
  const currentPath = window.location.pathname
  return (
    <header className="topbar">
      <a href="/" className="brand">
        <span className="brand-mark">RC</span>
        <span>
          <strong>Review Catalog</strong>
          <small>evidence release lab</small>
        </span>
      </a>
      <nav aria-label="주요 페이지">
        {links.map(([to, label]) => (
          <a key={to} href={to} className={currentPath === to ? 'active' : ''}>
            {label}
          </a>
        ))}
      </nav>
    </header>
  )
}
