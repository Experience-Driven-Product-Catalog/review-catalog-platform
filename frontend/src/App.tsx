import { Navigation } from './components/Navigation'
import { CatalogPage } from './pages/CatalogPage'
import { DemoPage } from './pages/DemoPage'
import { HomePage } from './pages/HomePage'

export default function App() {
  const path = window.location.pathname
  const page = path === '/catalog' ? <CatalogPage /> : path === '/demo' ? <DemoPage /> : <HomePage />
  return (
    <>
      <Navigation />
      {page}
    </>
  )
}
