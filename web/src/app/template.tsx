/**
 * Re-mounted on every navigation (unlike layout.tsx), so each tab fades in gently instead of
 * appearing abruptly. The conversation itself lives in ChatProvider, above this, so it survives.
 */
export default function Template({ children }: { children: React.ReactNode }) {
  return (
    <div className="animate-in fade-in slide-in-from-bottom-1 flex flex-1 flex-col duration-200 ease-out motion-reduce:animate-none">
      {children}
    </div>
  );
}
