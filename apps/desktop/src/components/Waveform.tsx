interface WaveformProps {
  active: boolean;
}

export function Waveform({ active }: WaveformProps) {
  return (
    <div
      className={`waveform ${active ? "waveform--active" : ""}`}
      aria-hidden="true"
    >
      {[18, 30, 42, 28, 50, 34, 22].map((height, index) => (
        <span
          key={height}
          style={
            {
              "--bar-height": `${height}px`,
              "--delay": `${index * 70}ms`,
            } as React.CSSProperties
          }
        />
      ))}
    </div>
  );
}
