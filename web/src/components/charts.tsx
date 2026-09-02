/** Inline SVG charts: monthly word bars and the fidelity-by-version line chart. */

import type { JSX } from 'preact';
import { useState } from 'preact/hooks';
import { fmtInt, fmtMonth, fmtPct } from '../format';
import { S } from '../strings';
import type { EvalVersion, MonthStats } from '../types';

export function MonthBars({ months }: { months: MonthStats[] }): JSX.Element {
  const data = [...months].sort((a, b) => (a.month < b.month ? -1 : 1)).slice(-36);
  const [hover, setHover] = useState<number | null>(null);
  if (data.length === 0) return <p class="muted small">{S.corpus.noMonths}</p>;
  const W = 640;
  const H = 120;
  const padB = 22;
  const padT = 18;
  const gap = 2;
  const max = Math.max(1, ...data.map((d) => d.words));
  const bw = (W - gap * (data.length - 1)) / data.length;
  const first = data[0];
  const last = data[data.length - 1];
  const hovered = hover === null ? null : data[hover];
  return (
    <svg
      class="chart"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={S.corpus.byMonth}
      onMouseLeave={() => setHover(null)}
    >
      <line class="axis" x1={0} x2={W} y1={H - padB} y2={H - padB} />
      {data.map((d, i) => {
        const h = Math.max(1, ((H - padB - padT) * d.words) / max);
        const x = i * (bw + gap);
        return (
          <g key={d.month}>
            <rect
              x={x}
              y={H - padB - h}
              width={bw}
              height={h}
              rx={Math.min(3, bw / 2)}
              class={`bar ${i === data.length - 1 ? 'latest' : ''}`}
            />
            <rect
              x={x - gap / 2}
              y={0}
              width={bw + gap}
              height={H}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onTouchStart={() => setHover(i)}
            >
              <title>{`${fmtMonth(d.month)}: ${fmtInt(d.words)} ${S.corpus.words}`}</title>
            </rect>
          </g>
        );
      })}
      {first && (
        <text x={0} y={H - 6} text-anchor="start">
          {fmtMonth(first.month)}
        </text>
      )}
      {last && data.length > 1 && (
        <text x={W} y={H - 6} text-anchor="end">
          {fmtMonth(last.month)}
        </text>
      )}
      {hovered && hover !== null ? (
        <text
          x={Math.min(W - 4, Math.max(60, hover * (bw + gap) + bw / 2))}
          y={12}
          text-anchor="middle"
          style="fill:var(--ink)"
        >
          {`${fmtMonth(hovered.month)} · ${fmtInt(hovered.words)}`}
        </text>
      ) : (
        <text x={W} y={12} text-anchor="end">
          {`${S.corpus.words} / ${S.corpus.byMonth.toLowerCase().replace('by ', '')}`}
        </text>
      )}
    </svg>
  );
}

interface Series {
  key: 'blind_accuracy' | 'discriminator_accuracy' | 'voice_cosine';
  label: string;
  color: string;
}

const SERIES: readonly Series[] = [
  { key: 'blind_accuracy', label: S.train.seriesBlind, color: 'var(--series-1)' },
  { key: 'discriminator_accuracy', label: S.train.seriesDisc, color: 'var(--series-2)' },
  { key: 'voice_cosine', label: S.train.seriesVoice, color: 'var(--series-3)' },
];

export function FidelityChart({ versions }: { versions: EvalVersion[] }): JSX.Element {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640;
  const H = 260;
  const padL = 42;
  const padR = 110;
  const padT = 16;
  const padB = 34;
  const n = versions.length;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const x = (i: number): number => (n <= 1 ? padL + innerW / 2 : padL + (innerW * i) / (n - 1));
  const y = (v: number): number => padT + innerH * (1 - Math.max(0, Math.min(1, v)));
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const hovered = hover === null ? null : versions[hover];

  return (
    <div class="stack">
      <svg
        class="chart"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={S.train.fidelity}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line class="grid" x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} />
            <text x={padL - 8} y={y(t) + 4} text-anchor="end">
              {fmtPct(t)}
            </text>
          </g>
        ))}
        <line class="target" x1={padL} x2={W - padR} y1={y(0.5)} y2={y(0.5)} />
        <text x={W - padR + 8} y={y(0.5) + 4}>
          {S.train.target}
        </text>
        {versions.map((v, i) => (
          <text key={v.version} x={x(i)} y={H - 10} text-anchor="middle">
            {v.version}
          </text>
        ))}
        {SERIES.map((s) => {
          const pts = versions.map((v, i) => ({ i, v: v[s.key] }));
          const segments: string[] = [];
          let cur: string[] = [];
          for (const p of pts) {
            if (typeof p.v === 'number') cur.push(`${x(p.i)},${y(p.v)}`);
            else if (cur.length) {
              segments.push(cur.join(' '));
              cur = [];
            }
          }
          if (cur.length) segments.push(cur.join(' '));
          const lastPt = [...pts].reverse().find((p) => typeof p.v === 'number');
          return (
            <g key={s.key}>
              {segments.map((seg, k) => (
                <polyline
                  key={k}
                  points={seg}
                  fill="none"
                  stroke={s.color}
                  stroke-width={2}
                  stroke-linejoin="round"
                  stroke-linecap="round"
                />
              ))}
              {pts.map((p) =>
                typeof p.v === 'number' ? (
                  <circle
                    key={p.i}
                    cx={x(p.i)}
                    cy={y(p.v)}
                    r={4}
                    fill={s.color}
                    stroke="var(--bg-elev)"
                    stroke-width={2}
                  />
                ) : null,
              )}
              {lastPt && typeof lastPt.v === 'number' && s.key !== 'blind_accuracy' && (
                <text x={x(lastPt.i) + 8} y={y(lastPt.v) + 4} style={`fill:${s.color}`}>
                  {s.label}
                </text>
              )}
            </g>
          );
        })}
        {versions.map((v, i) => (
          <rect
            key={`h-${v.version}`}
            x={x(i) - (n <= 1 ? innerW / 2 : innerW / (n - 1) / 2)}
            y={padT}
            width={n <= 1 ? innerW : innerW / (n - 1)}
            height={innerH}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
            onTouchStart={() => setHover(i)}
          />
        ))}
        {hovered && hover !== null && (
          <g class="tooltip" transform={`translate(${Math.min(x(hover) + 10, W - 190)}, ${padT})`}>
            <rect width={180} height={74} rx={8} />
            <text x={10} y={18} style="font-weight:600">
              {hovered.version}
            </text>
            <text x={10} y={34}>
              {`${S.train.seriesBlind}: ${fmtPct(hovered.blind_accuracy)}${
                hovered.blind_n ? ` (n=${hovered.blind_n})` : ''
              }`}
            </text>
            <text x={10} y={50}>
              {`${S.train.seriesDisc}: ${fmtPct(hovered.discriminator_accuracy)}`}
            </text>
            <text x={10} y={66}>
              {`${S.train.seriesVoice}: ${fmtPct(hovered.voice_cosine)}`}
            </text>
          </g>
        )}
      </svg>
      <div class="legend">
        {SERIES.map((s) => (
          <span key={s.key} class="legend-item">
            <span class="legend-swatch" style={`background:${s.color}`} />
            {s.label}
          </span>
        ))}
        <span class="legend-item">
          <span
            class="legend-swatch"
            style="background:transparent;border-top:2px dashed var(--ink-3);height:0"
          />
          {S.train.target}
        </span>
      </div>
    </div>
  );
}
