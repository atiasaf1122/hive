/**
 * Bottom composer for the project view.
 *
 *   ┌─────────────────────────────────────────────────────────────┐
 *   │ textarea — auto-grows up to ~10 lines                       │
 *   ├─────────────────────────────────────────────────────────────┤
 *   │ Ctrl + Enter to send    Shift + Enter for newline    [Send] │
 *   └─────────────────────────────────────────────────────────────┘
 *
 * If the textarea starts with "/", the SlashMenu overlay appears with
 * keyboard nav. Some slash commands are handled client-side (/clear,
 * /close); everything else is sent as a normal message and the
 * orchestrator decides what to do with it.
 */
import { IconArrowUp } from '@tabler/icons-react'
import { useMemo, useRef, useState } from 'react'
import { api } from '../../lib/api'
import { useSessions } from '../../stores/sessions'
import { filterCommands, SLASH_COMMANDS, SlashMenu, type SlashCommand } from './SlashMenu'

interface Props {
  sessionId: string
  disabled?: boolean
}

export function Composer({ sessionId, disabled }: Props) {
  const [text, setText] = useState('')
  const [selectedSlash, setSelectedSlash] = useState(0)
  const [sending, setSending] = useState(false)
  const taRef = useRef<HTMLTextAreaElement | null>(null)

  const appendUser = useSessions((s) => s.appendUserMessage)

  const showSlash = text.startsWith('/') && !text.includes('\n')
  const slashMatches = useMemo(() => (showSlash ? filterCommands(text) : []), [text, showSlash])

  function autosize() {
    const el = taRef.current
    if (!el) return
    el.style.height = '0'
    el.style.height = Math.min(el.scrollHeight, 220) + 'px'
  }

  function setBody(next: string) {
    setText(next)
    setSelectedSlash(0)
    window.setTimeout(autosize, 0)
  }

  async function send() {
    const body = text.trim()
    if (!body || sending) return

    // Client-side slash commands intercept here. Anything not matched falls
    // through as a normal message — the orchestrator can decide.
    if (body === '/clear') {
      setBody('')
      // History clearing is purely a UX-only no-op for now (backend keeps it).
      return
    }
    if (body === '/close') {
      setBody('')
      setSending(true)
      try {
        await api.post(`/api/sessions/${sessionId}/close`)
      } finally {
        setSending(false)
      }
      return
    }

    appendUser(sessionId, body)
    setBody('')
    setSending(true)
    try {
      await api.post(`/api/sessions/${sessionId}/message`, { text: body })
    } finally {
      setSending(false)
    }
  }

  function pickSlash(cmd: SlashCommand) {
    if (cmd.body.endsWith(' ')) {
      // Commands that take a parameter — keep the input open.
      setBody(cmd.body)
      taRef.current?.focus()
    } else {
      // Commands with no parameter — fire immediately on pick.
      setText(cmd.body)
      window.setTimeout(() => void send(), 0)
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (showSlash && slashMatches.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedSlash((i) => Math.min(slashMatches.length - 1, i + 1))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedSlash((i) => Math.max(0, i - 1))
        return
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        pickSlash(slashMatches[selectedSlash])
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setBody('')
        return
      }
    }
    if (e.key === 'Enter') {
      if (e.shiftKey) return // newline
      e.preventDefault()
      void send()
    }
  }

  return (
    <div className="border-t border-line bg-bg">
      <div className="max-w-[760px] mx-auto px-6 py-4">
        <div className="relative">
          {showSlash && (
            <SlashMenu
              query={text}
              selectedIndex={selectedSlash}
              onSelect={pickSlash}
              onMove={(d) =>
                setSelectedSlash((i) =>
                  Math.max(0, Math.min(slashMatches.length - 1, i + d)),
                )
              }
            />
          )}

          <div className="card p-3 focus-within:border-accent transition-colors">
            <textarea
              ref={taRef}
              value={text}
              onChange={(e) => setBody(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={disabled}
              placeholder={
                disabled
                  ? 'Project closed — start a new one from Projects.'
                  : 'Reply to the orchestrator…   type / for commands'
              }
              rows={1}
              className="w-full bg-transparent text-ink placeholder:text-ink-faint outline-none resize-none text-[14px] leading-relaxed"
            />

            <div className="flex items-center gap-1 mt-2 pt-2 border-t border-line text-ink-faint">
              <div className="flex-1" />

              <div className="text-[11px] text-ink-faint mr-3 hidden sm:block">
                Enter to send · Shift + Enter for newline
              </div>
              <button
                type="button"
                onClick={() => void send()}
                disabled={!text.trim() || sending || disabled}
                className="btn-primary inline-flex items-center gap-1 disabled:opacity-40 disabled:cursor-not-allowed h-8 px-3 text-xs"
              >
                <span>{sending ? '…' : 'Send'}</span>
                <IconArrowUp size={14} strokeWidth={1.75} />
              </button>
            </div>
          </div>
        </div>

        {!showSlash && (
          <div className="text-[11px] text-ink-faint mt-2 text-center">
            {SLASH_COMMANDS.length} slash commands · type / to see them
          </div>
        )}
      </div>
    </div>
  )
}

