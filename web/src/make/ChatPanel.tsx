import { useRef, useState } from "react";
import type { JSX } from "react";
import type { MakeMessage } from "./makeApi";
import type { JobLine } from "./useJobStream";

export default function ChatPanel({ messages, lines, attempt, state, busy, onSend }: {
  messages: MakeMessage[];
  lines: JobLine[];
  attempt: number | null;
  state: string | null;
  busy: boolean;
  onSend: (text: string, file: File | null) => Promise<void>;
}): JSX.Element {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function submit() {
    const body = text.trim();
    if (!body || busy) return;
    setErr(null);
    try {
      await onSend(body, file);
      setText("");
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
    } catch (error) {
      setErr(error instanceof Error ? error.message : "send failed");
    }
  }

  return (
    <aside className="make-chat">
      <div className="make-messages" data-make="messages">
        {messages.map((message) => (
          <div key={message.id} className={`make-msg ${message.role}`}>
            <b>{message.role}</b>
            <p>{message.text}</p>
          </div>
        ))}
        {(state || lines.length > 0) && (
          <div className="make-log" data-make="status" aria-live="polite">
            {state && <div>status {state}{attempt != null && attempt > 0 ? ` · attempt ${attempt}` : ""}</div>}
            {lines.map((line, index) => <div key={`${index}-${line.text}`}>{line.text}</div>)}
          </div>
        )}
      </div>
      <form className="make-compose" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <textarea
          data-make="composer"
          rows={3}
          placeholder="Describe a detail, or a change…"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <div className="make-compose-row">
          <label className="make-file">
            Attach
            <input
              ref={fileRef}
              data-make="attach"
              type="file"
              accept="image/png,image/jpeg,application/pdf"
              onChange={(event) => setFile(event.target.files?.[0] || null)}
            />
          </label>
          {file && <span className="make-file-name">{file.name}</span>}
          <button type="submit" className="primary" data-make="send" disabled={busy || !text.trim()}>
            {busy ? "Working…" : "Send"}
          </button>
        </div>
        {err && <p className="make-err">{err}</p>}
      </form>
    </aside>
  );
}
