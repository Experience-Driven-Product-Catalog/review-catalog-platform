const links = [
  { to: '/', number: '01', label: '프로젝트' },
  { to: '/catalog', number: '02', label: '상품 보고서' },
  { to: '/demo', number: '03', label: '리뷰 데모' },
]

export function Navigation() {
  const currentPath = window.location.pathname
  return (
    <header className="topbar">
      <a href="/" className="brand">
        <span className="brand-mark">RC</span>
        <span>
          <strong>Review Catalog</strong>
          <small>Experience-Driven Product Catalog</small>
        </span>
      </a>
      <nav aria-label="주요 페이지">
        {links.map(({ to, number, label }) => (
          <a
            key={to}
            href={to}
            className={currentPath === to ? 'active' : ''}
            aria-current={currentPath === to ? 'page' : undefined}
          >
            <span className="nav-number">{number}</span>
            <span>{label}</span>
          </a>
        ))}
      </nav>
    </header>
  )
}
