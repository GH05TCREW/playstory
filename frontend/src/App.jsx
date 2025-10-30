import React from 'react'
import { useEffect, useRef, useState } from 'react'
import './styles.css'

const API = 'http://localhost:8000'

export default function App() {
  const [storyId, setStoryId] = useState(() => Math.random().toString(36).slice(2))
  const [loadStoryId, setLoadStoryId] = useState('')
  const [basePrompt, setBasePrompt] = useState(
    'A tense anime chase in a rainy uptown street at night. Cinematography: handheld medium shot following the protagonist from behind. Mood: gritty, neon reflections on wet asphalt.'
  )
  const [currentNode, setCurrentNode] = useState(null)
  const [jobId, setJobId] = useState(null)
  const [status, setStatus] = useState('idle')
  const [videoUrl, setVideoUrl] = useState('')
  const [options, setOptions] = useState([])
  const [optionsSource, setOptionsSource] = useState('')
  const [videoEnded, setVideoEnded] = useState(false)
  const [error, setError] = useState('')
  const [mutedByPolicy, setMutedByPolicy] = useState(false)
  const [userInteracted, setUserInteracted] = useState(false)
  const [choicesVisible, setChoicesVisible] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [copied, setCopied] = useState(false)
  const [includeContext, setIncludeContext] = useState(false)
  const pollRef = useRef(null)
  const videoRef = useRef(null)
  const playedOnceRef = useRef(false)
  const stopAutoplayRef = useRef(false)
  const showLoader = status === 'starting' || status === 'processing'

  function startPoll(job) {
    if (!job) return
    setStatus('processing')
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API}/jobs/${job}`)
        if (!r.ok) {
          clearInterval(pollRef.current)
          setStatus('failed')
          const t = await r.text()
          setError(t || 'Polling failed')
          return
        }
        const j = await r.json()
        if (j.status === 'completed') {
          clearInterval(pollRef.current)
          setStatus('completed')
          const absUrl = j.video_url && j.video_url.startsWith('http')
            ? j.video_url
            : `${API}${j.video_url || ''}`
          setVideoUrl(absUrl)
          setOptions(j.options || [])
          setOptionsSource(j.options_source || '')
          setVideoEnded(false)
        } else if (j.status === 'retrying' && j.new_job_id) {
          // Switch to the new job (e.g., after backend softened a blocked prompt)
          clearInterval(pollRef.current)
          setStatus('processing')
          setJobId(j.new_job_id)
          startPoll(j.new_job_id)
        } else if (j.status === 'failed' || j.status === 'error') {
          clearInterval(pollRef.current)
          setStatus('failed')
          const err = j.error
          let msg = 'Generation failed'
          if (typeof err === 'string') {
            msg = err
          } else if (err && typeof err === 'object') {
            // Prefer human-friendly message when available
            const code = err.code || err.type
            const m = err.message || err.detail || err.msg
            msg = m ? (code ? `${code}: ${m}` : m) : JSON.stringify(err)
          }
          setError(msg)
        }
      } catch (e) {
        clearInterval(pollRef.current)
        setStatus('failed')
        setError(String(e))
      }
    }, 10000) // Poll every 10 seconds (OpenAI recommendation: 10-20 seconds)
  }

  async function onStart() {
    setUserInteracted(true)
    stopAutoplayRef.current = false
    setChoicesVisible(false)
    setOptions([])
    setOptionsSource('')
    setVideoUrl('')
    setStatus('starting')
    setVideoEnded(false)
    setError('')
    const r = await fetch(`${API}/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ story_id: storyId, base_prompt: basePrompt })
    })
    if (!r.ok) {
      let msg = ''
      try { const d = await r.json(); msg = d.detail || JSON.stringify(d) } catch { msg = await r.text() }
      setStatus('failed')
      setError(msg || 'Start failed')
      return
    }
    const j = await r.json()
    if (!j?.job_id) {
      setStatus('failed')
      setError('No job id returned from backend')
      return
    }
    setCurrentNode(j.node_id)
    setJobId(j.job_id)
    startPoll(j.job_id)
  }

  function copyStoryId() {
    navigator.clipboard.writeText(storyId)
    setCopied(true)
    setTimeout(() => setCopied(false), 1000)
  }

  async function loadStory() {
    if (!loadStoryId.trim()) {
      setError('Please enter a story ID')
      return
    }
    setError('')
    setStatus('loading')
    try {
      const r = await fetch(`${API}/stories/${loadStoryId.trim()}`)
      if (!r.ok) {
        setStatus('failed')
        setError('Story not found')
        return
      }
      const data = await r.json()
      if (!data.nodes || data.nodes.length === 0) {
        setStatus('failed')
        setError('Story has no nodes')
        return
      }
      
      // Find the latest completed node
      const completedNodes = data.nodes.filter(n => n.status === 'completed')
      if (completedNodes.length === 0) {
        setStatus('failed')
        setError('Story has no completed videos')
        return
      }
      
      const latestNode = completedNodes[completedNodes.length - 1]
      
      // Update state with loaded story
      setStoryId(loadStoryId.trim())
      setCurrentNode(latestNode.id)
      setBasePrompt(data.nodes[0].prompt || basePrompt)
      
      // Set the video URL - handle both absolute and relative paths
      let videoPath = latestNode.video_path
      if (!videoPath) {
        setStatus('failed')
        setError('Latest node has no video path')
        return
      }
      
      // Extract relative path if it's an absolute path
      // Example: C:\Users\...\media\videos\storyId\nodeId.mp4 -> /media/videos/storyId/nodeId.mp4
      let relativePath = videoPath
      if (videoPath.includes('\\media\\videos\\') || videoPath.includes('/media/videos/')) {
        const match = videoPath.match(/(?:media[\\\/]videos[\\\/].+)$/)
        if (match) {
          relativePath = '/' + match[0].replace(/\\/g, '/')
        }
      } else if (!videoPath.startsWith('/') && !videoPath.startsWith('http')) {
        // If it's just a filename, construct the full path
        relativePath = `/media/videos/${loadStoryId.trim()}/${videoPath}`
      }
      
      const absUrl = relativePath.startsWith('http') 
        ? relativePath 
        : `${API}${relativePath}`
      
      console.log('Loading video from:', absUrl)
      setVideoUrl(absUrl)
      
      // Load options if available
      if (latestNode.options) {
        try {
          const opts = JSON.parse(latestNode.options)
          setOptions(opts)
          setOptionsSource('cached')
        } catch {}
      }
      
      setStatus('completed')
      setVideoEnded(false)
      setChoicesVisible(true)
      setLoadStoryId('')
    } catch (err) {
      setStatus('failed')
      setError(err.message || 'Failed to load story')
    }
  }

  async function onContinue(opt) {
    setUserInteracted(true)
    stopAutoplayRef.current = false
    setChoicesVisible(false)
    setOptions([])
    setOptionsSource('')
    setStatus('starting')
    setVideoEnded(false)
    setError('')
    const r = await fetch(`${API}/continue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        story_id: storyId,
        parent_node_id: currentNode,
        choice_label: opt.label,
        sora_prompt: opt.sora_prompt,
        include_context: includeContext,
      })
    })
    if (!r.ok) {
      let msg = ''
      try { const d = await r.json(); msg = d.detail || JSON.stringify(d) } catch { msg = await r.text() }
      setStatus('failed')
      setError(msg || 'Continue failed')
      return
    }
    const j = await r.json()
    if (!j?.job_id) {
      setStatus('failed')
      setError('No job id returned from backend')
      return
    }
    setCurrentNode(j.node_id)
    setJobId(j.job_id)
    startPoll(j.job_id)
  }

  useEffect(() => {
    // Reset ended state when video URL changes
    setVideoEnded(false)
    setMutedByPolicy(false)
    setChoicesVisible(false)
    playedOnceRef.current = false
    stopAutoplayRef.current = false
    // Try to autoplay with sound when a new video loads
    const v = videoRef.current
    if (v && videoUrl) {
      const tryPlay = async () => {
        try {
          if (playedOnceRef.current || stopAutoplayRef.current) return
          v.muted = false
          v.volume = 1
          await v.play()
          setMutedByPolicy(false)
          playedOnceRef.current = true
        } catch (err) {
          // Autoplay with audio blocked; fall back to muted autoplay
          try {
            if (playedOnceRef.current || stopAutoplayRef.current) return
            v.muted = true
            await v.play()
            setMutedByPolicy(true)
            playedOnceRef.current = true
          } catch (e2) {
            // If even muted autoplay fails, leave it to user interaction
            setMutedByPolicy(true)
          }
        }
      }
      // If user has interacted at least once, most browsers allow audio
      // Attempt immediately; if it fails, we'll also re-attempt on 'canplay'.
      tryPlay()
    }
  }, [videoUrl])

  const onCanPlay = async () => {
    // On some platforms, playback is only possible after canplay fires
    const v = videoRef.current
    if (!v) return
    if (!v.paused) return
    if (videoEnded) return // don't restart after ending
    if (playedOnceRef.current) return
    if (stopAutoplayRef.current) return
    try {
      v.muted = false
      v.volume = 1
      await v.play()
      setMutedByPolicy(false)
      playedOnceRef.current = true
    } catch (err) {
      try {
        v.muted = true
        await v.play()
        setMutedByPolicy(true)
        playedOnceRef.current = true
      } catch {}
    }
  }

  const unmuteAndPlay = async () => {
    const v = videoRef.current
    if (!v) return
    if (stopAutoplayRef.current) return
    try {
      v.muted = false
      v.volume = 1
      await v.play()
      setMutedByPolicy(false)
    } catch {}
  }

  return (
    <div className="app dark">
      <div className="layout">
        <aside className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
          <button className="collapse-toggle" onClick={() => setSidebarCollapsed(!sidebarCollapsed)} title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
            <span className="toggle-icon">{sidebarCollapsed ? '→' : '←'}</span>
          </button>

          <h1 className="app-title">PlayStory</h1>

          <div className="field">
            <label>Current Story ID</label>
            <div className="id-row">
              <code className="id-badge">{storyId}</code>
              <button className="copy-icon-btn" onClick={copyStoryId} title="Copy story ID">
                {copied ? (
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="3,8 6,11 13,4"/>
                  </svg>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="4" y="4" width="9" height="9" rx="1"/>
                    <path d="M3 11V3a1 1 0 0 1 1-1h8"/>
                  </svg>
                )}
              </button>
            </div>
          </div>

          <div className="field">
            <label>Load Existing Story</label>
            <div className="load-story-row">
              <input 
                type="text" 
                placeholder="Enter story ID" 
                value={loadStoryId} 
                onChange={(e) => setLoadStoryId(e.target.value)}
                onKeyPress={(e) => e.key === 'Enter' && loadStory()}
              />
              <button className="load-btn" onClick={loadStory} disabled={status === 'processing' || status === 'loading'}>
                Load
              </button>
            </div>
          </div>

          <div className="field">
            <label>Base prompt</label>
            <textarea value={basePrompt} onChange={(e) => setBasePrompt(e.target.value)} rows={8} />
          </div>

          <div className="field checkbox-field">
            <label>
              <input 
                type="checkbox" 
                checked={includeContext} 
                onChange={(e) => setIncludeContext(e.target.checked)}
              />
              <span>Include story context in Sora prompts</span>
            </label>
            <small className="field-hint">Adds a brief summary of recent story beats to help Sora maintain narrative continuity</small>
          </div>

          <button className="primary" onClick={onStart} disabled={status === 'processing'}>Start</button>

          <div className="status">
            <span>Status: {status}</span>
            {optionsSource && (
              <span className="subtle">options: {optionsSource}</span>
            )}
          </div>
          {error && (
            <div className="error">{typeof error === 'string' ? error : (error?.message || JSON.stringify(error))}</div>
          )}
        </aside>

        <main className="main">
          <div className="video-wrap">
            {videoUrl ? (
              <video
                ref={videoRef}
                key={videoUrl}
                src={videoUrl}
                // No native controls; game-like presentation
                // Autoplay is managed programmatically above
                playsInline
                preload="metadata"
                onEnded={() => {
                  setVideoEnded(true)
                  setChoicesVisible(true)
                  stopAutoplayRef.current = true
                  try { videoRef.current?.pause() } catch {}
                }}
                onError={(e) => {
                  console.error('Video error:', e)
                  console.error('Video URL:', videoUrl)
                  console.error('Video element error:', e.target?.error)
                  setError(`Video failed to load: ${videoUrl}`)
                }}
                onCanPlay={onCanPlay}
                className="video"
              />
            ) : (!showLoader && (
              <div className="video-placeholder">Start to generate a scene…</div>
            ))}

            {showLoader && (
              <div className="loading-overlay">
                <div className="spinner" />
              </div>
            )}

            {/* If autoplay with audio was blocked, show a simple unmute affordance */}
            {videoUrl && !showLoader && !videoEnded && mutedByPolicy && (
              <button className="sound-overlay" onClick={unmuteAndPlay}>
                Tap for sound
              </button>
            )}

            {options.length > 0 && choicesVisible && !showLoader && (
              <div className="options-overlay">
                {options.map((opt, idx) => (
                  <button key={idx} className="option-card" onClick={() => onContinue(opt)}>
                    <span className="pill">{idx + 1}</span>
                    <span className="option-text">{opt.label}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
