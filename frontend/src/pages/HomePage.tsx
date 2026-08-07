import { useEffect, useRef, useState } from 'react'
import { MarkdownView } from '../components/MarkdownView'
import { api } from '../lib/api'

const chapters = [
  { id: 'overview', number: '01', label: 'Overview' },
  { id: 'experiment', number: '02', label: 'Experiment' },
  { id: 'release', number: '03', label: 'Release' },
  { id: 'about-me', number: '04', label: 'About me' },
]

const profileImageUrl = 'https://raw.githubusercontent.com/Experience-Driven-Product-Catalog/review-catalog-platform/refs/heads/main/assets/profile.jpg'

function ProfileResume({ markdown }: { markdown: string }) {
  const profileMarkdown = markdown.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '')
  const [intro, ...sections] = profileMarkdown.split(/(?=^## )/gm).filter(Boolean)

  return (
    <div className="profile-page">
      <header className="profile-cover">
        <div className="profile-photo-wrap">
          <span className="profile-tape" aria-hidden="true" />
          <div className="profile-photo-frame">
            <img src={profileImageUrl} alt="필자 프로필" />
          </div>
          <span className="profile-sticker">HI, THERE!</span>
        </div>
        <div className="profile-cover-copy">
          <p className="eyebrow">04 / ABOUT ME</p>
          <MarkdownView markdown={intro} />
          <div className="profile-motto" aria-label="Learn, build, share">
            <span>LEARN</span><i />
            <span>BUILD</span><i />
            <span>SHARE</span>
          </div>
        </div>
      </header>
      <div className="profile-sections">
        {sections.map((section, index) => (
          <section
            className={`profile-section ${index === 0 ? 'profile-facts' : 'profile-achievements'}`}
            key={section.slice(0, 48)}
          >
            <span className="profile-section-number">0{index + 1}</span>
            <MarkdownView markdown={section} />
          </section>
        ))}
      </div>
    </div>
  )
}

export function HomePage() {
  const [activeChapter, setActiveChapter] = useState(0)
  const [markdown, setMarkdown] = useState('')
  const [sourceUrl, setSourceUrl] = useState<string | null>(null)
  const [error, setError] = useState('')
  const panelRef = useRef<HTMLElement>(null)
  const chapter = chapters[activeChapter]

  useEffect(() => {
    let cancelled = false
    api.aboutSection(chapter.id)
      .then((result) => {
        if (!cancelled) {
          setMarkdown(result.markdown)
          setSourceUrl(result.source_url)
        }
      })
      .catch((reason) => {
        if (!cancelled) setError(String(reason))
      })
    return () => { cancelled = true }
  }, [chapter.id])

  function selectChapter(index: number) {
    setMarkdown('')
    setSourceUrl(null)
    setError('')
    panelRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
    setActiveChapter(index)
  }

  const nextChapter = chapters[activeChapter + 1]

  return (
    <main className={`page home-page ${activeChapter === 0 ? '' : 'compact-hero'}`}>
      <section className="hero">
        <p className="eyebrow">HTTPS · GITHUB ACTIONS · CODEDEPLOY</p>
        <h1>리뷰 근거가<br />배포 가능한 카탈로그가 되는 과정</h1>
        <p>
          추출, 정규화 후보 판정, immutable snapshot과 규칙 기반 보고서를 하나의 release로 묶고
          검증된 변경만 HTTPS 카탈로그에 배포합니다.
        </p>
      </section>
      <section className="readme-panel" ref={panelRef}>
        <div className="chapter-tabs-bar">
          <div className="chapter-tabs" role="tablist" aria-label="프로젝트 문서 읽기 순서">
            {chapters.map((item, index) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={activeChapter === index}
                className={activeChapter === index ? 'selected' : ''}
                onClick={() => selectChapter(index)}
              >
                <span>{item.number}</span>
                {item.label}
              </button>
            ))}
          </div>
        </div>
        <div className="chapter-content">
          {error ? (
            <div className="error-card">{error}</div>
          ) : markdown ? (
            chapter.id === 'about-me' ? (
              <ProfileResume markdown={markdown} />
            ) : <MarkdownView markdown={markdown} sourceUrl={sourceUrl} />
          ) : <div className="skeleton">문서를 불러오는 중…</div>}
        </div>
        {markdown && (
          <footer className="chapter-footer">
            {nextChapter ? (
              <button type="button" className="chapter-next" onClick={() => selectChapter(activeChapter + 1)}>
                <span><small>NEXT · {nextChapter.number}</small>{nextChapter.label}</span>
                <svg viewBox="0 0 64 24" aria-hidden="true">
                  <path d="M1 12h57M49 3l10 9-10 9" />
                </svg>
              </button>
            ) : (
              <button type="button" className="chapter-next restart" onClick={() => selectChapter(0)}>
                <span><small>READ AGAIN · 01</small>Overview</span>
                <svg viewBox="0 0 64 24" aria-hidden="true">
                  <path d="M63 12H6M15 3l-10 9 10 9" />
                </svg>
              </button>
            )}
          </footer>
        )}
      </section>
    </main>
  )
}
