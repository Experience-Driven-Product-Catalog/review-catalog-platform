import ReactMarkdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'

function resolveImageSource(src: string | undefined, sourceUrl: string | null) {
  if (!src || !sourceUrl) return src
  try {
    return new URL(src, sourceUrl).toString()
  } catch {
    return src
  }
}

export function MarkdownView({ markdown, sourceUrl = null }: { markdown: string; sourceUrl?: string | null }) {
  return (
    <article className="markdown-view">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, rehypeSanitize]}
        components={{
          img: ({ src, alt, ...props }) => (
            <img {...props} src={resolveImageSource(src, sourceUrl)} alt={alt ?? ''} loading="lazy" />
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </article>
  )
}
