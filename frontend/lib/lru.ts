// A small in-memory cache with a fixed number of entries and a time to live.
//
// Used for backend responses whose URL the visitor chooses (/fit and the
// filtered grant index): the combinations are unbounded, so Next's data
// cache, which keeps one entry per URL on disk with no upper limit, would let
// anyone fill it. This cache holds at most `maxEntries` responses, evicting
// the least recently used, and lives only in this process's memory.
//
// Pure and dependency-free so it can be tested with `node --test`.

export class LruCache<V> {
  private readonly entries = new Map<string, { value: V; expires: number }>();
  private readonly maxEntries: number;
  private readonly ttlMs: number;
  private readonly now: () => number;

  constructor(maxEntries: number, ttlMs: number, now: () => number = Date.now) {
    this.maxEntries = maxEntries;
    this.ttlMs = ttlMs;
    this.now = now;
  }

  get(key: string): V | undefined {
    const entry = this.entries.get(key);
    if (!entry) return undefined;
    if (entry.expires <= this.now()) {
      this.entries.delete(key);
      return undefined;
    }
    // Re-insert so that Map order is least recently used first.
    this.entries.delete(key);
    this.entries.set(key, entry);
    return entry.value;
  }

  set(key: string, value: V): void {
    this.entries.delete(key);
    this.entries.set(key, { value, expires: this.now() + this.ttlMs });
    while (this.entries.size > this.maxEntries) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
    }
  }

  get size(): number {
    return this.entries.size;
  }
}
