import { Signer } from "./signer";

const BASE_URL = "https://example.com";

export async function fetchSigned(path: string): Promise<Response> {
  return fetch(`${BASE_URL}${path}`);
}

export class Client {
  constructor(private signer: Signer) {}

  sign(value: string): string {
    return this.signer.sign(value);
  }
}
