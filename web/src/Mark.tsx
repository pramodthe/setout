/* Setout's mark: an I-joist in section, the part the landing story starts from. */

import type { JSX } from "react";

export default function Mark({ className }: { className?: string }): JSX.Element {
  return <svg className={className} viewBox="0 0 24 28" fill="none" aria-hidden="true"><path d="M3 2.5h18v4.5h-7v14h7v4.5H3V21h7V7H3z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>;
}
