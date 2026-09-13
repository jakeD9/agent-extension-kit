import type { Principal } from "@agent-extension-kit/contracts";

export interface Authenticator {
  authenticate(authorization: string | undefined): Promise<Principal | null>;
}

export class StaticBearerAuthenticator implements Authenticator {
  constructor(private readonly principalsByToken: ReadonlyMap<string, Principal>) {}

  async authenticate(authorization: string | undefined): Promise<Principal | null> {
    if (!authorization?.startsWith("Bearer ")) return null;
    return this.principalsByToken.get(authorization.slice(7)) ?? null;
  }

  static fromJson(value: string): StaticBearerAuthenticator {
    const entries = JSON.parse(value) as Record<string, Principal>;
    return new StaticBearerAuthenticator(new Map(Object.entries(entries)));
  }
}
