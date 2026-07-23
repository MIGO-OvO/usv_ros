export class RingBuffer<T> {
  readonly capacity: number
  private readonly items: (T | undefined)[]
  private start = 0
  private count = 0

  constructor(capacity: number) {
    if (!Number.isInteger(capacity) || capacity < 1) throw new RangeError('capacity must be a positive integer')
    this.capacity = capacity
    this.items = new Array<T | undefined>(capacity)
  }

  get length() {
    return this.count
  }

  appendBatch(values: readonly T[]) {
    for (const value of values) {
      const index = (this.start + this.count) % this.capacity
      this.items[index] = value
      if (this.count < this.capacity) this.count += 1
      else this.start = (this.start + 1) % this.capacity
    }
  }

  clear() {
    this.items.fill(undefined)
    this.start = 0
    this.count = 0
  }

  toArray(_revision?: number): T[] {
    void _revision
    return Array.from({ length: this.count }, (_, index) => this.items[(this.start + index) % this.capacity] as T)
  }
}
