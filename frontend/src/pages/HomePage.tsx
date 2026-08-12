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

const hardSkills = [
  {
    level: '01',
    tools: '언어는 Python, SQL, Typescript, 도구는 React, FastAPI, Flutter를 주력합니다.',
    detail: '관련된 프로젝트가 다수 있으며 실무에서 바로 이해하고 사용할 수 있습니다.',
  },
  {
    level: '02',
    tools: 'C/C++, Pandas, Pytorch, PostgreSQL, Airflow, Figma를 다룰 수 있습니다.',
    detail: '직접 구현하고 디버깅하며 문제를 해결할 수 있으나, 세부 문법 및 API에 대한 레퍼런스 참고가 필요합니다.',
  },
  {
    level: '03',
    tools: 'Spark, Kafka, Flink, DBT, Docker, AWS(devops)는 코드를 이해할 수 있습니다.',
    detail: '작성된 코드를 보고 이해할 수 있으나 실질적 경험이 부족하여 숙지에 시간이 필요합니다.',
  },
]

function chapterIndexFromLocation() {
  const hash = window.location.hash.slice(1)
  const tab = new URLSearchParams(window.location.search).get('tab')
  const requestedChapter = (hash || tab || '').toLowerCase()
  const chapterIndex = chapters.findIndex((chapter) => chapter.id === requestedChapter)

  return chapterIndex === -1 ? 0 : chapterIndex
}

function ProfileResume({ markdown }: { markdown: string }) {
  const profileMarkdown = markdown.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '').replaceAll('>[!warning]', '>')
  const [intro, ...sections] = profileMarkdown.split(/(?=^## )/gm).filter(Boolean)
  const [facts, ...achievementSections] = sections
  const achievements = achievementSections.join('\n\n')

  return (
    <div className="profile-page">
      <header className="profile-cover">
        <div className="profile-photo-wrap">
          <span className="profile-tape" aria-hidden="true" />
          <div className="profile-photo-frame">
            <img src={profileImageUrl} alt="프로필" />
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
        {facts && (
          <section className="profile-section profile-facts">
            <span className="profile-section-number">01</span>
            <MarkdownView markdown={facts} />
          </section>
        )}
        <section className="profile-section profile-skills" aria-labelledby="hard-skills-heading">
          <span className="profile-section-number">02</span>
          <div className="hard-skills">
            <h2 id="hard-skills-heading">다룰 수 있는 언어와 도구</h2>
            <ol>
              {hardSkills.map((skill) => (
                <li key={skill.level}>
                  <span className="hard-skill-level">{skill.level}</span>
                  <div>
                    <strong>{skill.tools}</strong>
                    <p>{skill.detail}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>
        {achievements && (
          <section className="profile-section profile-achievements">
            <span className="profile-section-number">03</span>
            <MarkdownView markdown={achievements} />
          </section>
        )}
      </div>
    </div>
  )
}

export function HomePage() {
  const [activeChapter, setActiveChapter] = useState(chapterIndexFromLocation)
  const [markdown, setMarkdown] = useState('')
  const [sourceUrl, setSourceUrl] = useState<string | null>(null)
  const [error, setError] = useState('')
  const panelRef = useRef<HTMLElement>(null)
  const activeChapterRef = useRef(activeChapter)
  const chapter = chapters[activeChapter]

  function resetChapterPanel() {
    setMarkdown('')
    setSourceUrl(null)
    setError('')
    panelRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
  }

  useEffect(() => {
    activeChapterRef.current = activeChapter
  }, [activeChapter])

  useEffect(() => {
    function syncChapterWithLocation() {
      const chapterIndex = chapterIndexFromLocation()
      if (chapterIndex === activeChapterRef.current) return

      resetChapterPanel()
      setActiveChapter(chapterIndex)
    }

    window.addEventListener('hashchange', syncChapterWithLocation)
    window.addEventListener('popstate', syncChapterWithLocation)

    return () => {
      window.removeEventListener('hashchange', syncChapterWithLocation)
      window.removeEventListener('popstate', syncChapterWithLocation)
    }
  }, [])

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
    if (index === activeChapter) return

    const url = new URL(window.location.href)
    url.searchParams.delete('tab')
    url.hash = chapters[index].id
    window.history.pushState(null, '', url)

    resetChapterPanel()
    setActiveChapter(index)
  }

  const nextChapter = chapters[activeChapter + 1]

  return (
    <main className={`page home-page ${activeChapter === 0 ? '' : 'compact-hero'}`}>
      <section className="hero">
        <p className="eyebrow">Problem · Validate · Productize</p>
        <h1>리뷰 속<br />근거 있는<br />체감 속성을<br />비교·검색 가능한<br />카탈로그 시스템</h1>
        <p>
          고객의 합리적 선택을 위해 리뷰의 체감 속성을 구조화하는 가설을 세우고 실험으로 검증한 뒤, 운영 가능한 카탈로그 시스템을 구현하였습니다.
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
