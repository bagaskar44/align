export function ParserLimitations({ entries }: { entries: Array<Record<string, unknown>> }) {
  const groups = new Map<string, { message: string; count: number; sources: Set<string> }>();
  for (const entry of entries) {
    const message = String(entry.message ?? "Keterbatasan pembacaan sumber; rincian teknis tersedia di bawah");
    const key = `${String(entry.code ?? "")}:${message}`;
    const group = groups.get(key) ?? { message, count: 0, sources: new Set<string>() };
    group.count += 1;
    if (entry.source) group.sources.add(String(entry.source));
    groups.set(key, group);
  }
  if (!entries.length) return null;
  return <details className="parser-limitations"><summary>Ada {entries.length} catatan keterbacaan sumber · {groups.size} jenis</summary><ul>{[...groups].map(([key, group]) => <li key={key}><p>{group.message}{group.count > 1 ? ` (${group.count} catatan)` : ""}</p>{group.sources.size > 0 && <p>{[...group.sources].join(" · ")}</p>}</li>)}</ul><details><summary>Detail teknis catatan sumber</summary><pre>{JSON.stringify(entries, null, 2)}</pre></details></details>;
}
