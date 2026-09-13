# Contributing

Use Node.js 24 and the pnpm version declared by the repository. Keep changes inside one roadmap slice when possible and test behavior through public interfaces.

Before submitting a change, run:

```sh
pnpm check
pnpm test
pnpm build
```

New skills need positive trigger cases, negative trigger cases, and one end-to-end fixture. New providers implement existing ports and pass the shared contract suite rather than adding provider checks to core services.
