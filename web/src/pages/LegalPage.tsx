import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getLegalDoc } from '../lib/api'
import { ErrorState } from '../components/Feedback'

export default function LegalPage() {
  const { name = 'terms' } = useParams()
  const [text, setText] = useState('')
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let alive = true
    setError(null)
    getLegalDoc(name)
      .then((doc) => {
        if (alive) setText(doc.text)
      })
      .catch((err) => {
        if (alive) setError(err)
      })
    return () => {
      alive = false
    }
  }, [name])

  if (error) return <ErrorState message="无法加载条款文档" onRetry={() => setText('')} />
  if (!text) return <div className="legal-loading">加载中…</div>

  return (
    <div className="legal-page">
      <div className="legal-page-inner">
        <div className="legal-nav">
          <Link to="/legal/terms">服务条款</Link>
          <Link to="/legal/privacy">隐私政策</Link>
          <Link to="/login">返回登录</Link>
        </div>
        <article className="legal-content">
          {text.split('\n').map((line, index) => {
            if (line.startsWith('# ')) {
              return <h1 key={index}>{line.slice(2)}</h1>
            }
            if (line.startsWith('## ')) {
              return <h2 key={index}>{line.slice(3)}</h2>
            }
            if (!line.trim()) return <div key={index} className="legal-gap" />
            return <p key={index}>{line}</p>
          })}
        </article>
      </div>
    </div>
  )
}
