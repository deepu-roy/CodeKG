export class SampleClass {
  constructor(private name: string) {}

  getName(): string {
    return this.name;
  }

  sayHello(): void {
    console.log(`Hello, ${this.getName()}`);
  }
}

export function formatDate(date: Date): string {
  return date.toISOString();
}
