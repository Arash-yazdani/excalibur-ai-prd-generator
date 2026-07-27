# Security

## Threat model

EXCALIBUR is a **local, single-user tool**. The server binds to `127.0.0.1:4500` and the API is unauthenticated — that binding is the entire access control. Consequences worth understanding before you run it:

- **Don't expose the port.** Anything that can reach `:4500` can read every PRD, delete projects, and start runs against your credential. Don't port-forward it, don't bind it to `0.0.0.0`, and be careful on shared machines.
- **There is deliberately no CORS middleware.** A permissive `Access-Control-Allow-Origin` would let any web page you happen to visit read the API's responses while the server is running. The SPA is same-origin and needs no CORS headers.
- **Agent output is untrusted input.** Handoffs and artifacts are written by a model acting on text you pasted, so a prompt injection could try to emit HTML. Everything rendered in the browser passes through `render_markdown()` in `server.py`, which sanitizes with `nh3`.
- **The agents can write files.** They run with `permission_mode="acceptEdits"` inside the repo directory. Treat a run the way you'd treat running any code you didn't write.

## Credentials

EXCALIBUR never stores, logs, or transmits your credential. It reads whichever environment variable you set and hands the environment to the Claude Code CLI, which does the authenticating.

One exception worth knowing about: on macOS, in **subscription mode only**, `tools/auth_preflight.py` copies your OAuth token from the login Keychain to `~/.claude/.credentials.json` (mode `600`) because SDK subprocesses can't reach the Keychain. This moves a Keychain-protected secret onto disk. It does not happen in API-key, gateway, or cloud-provider mode.

Keep `.env` out of version control — it is in `.gitignore`, and the credential rows in `.env.example` are commented out.

## Reporting a vulnerability

Open a [GitHub issue](https://github.com/arashyazd-bot/excalibur-ai-prd-generator/issues) for anything low-risk. For something that shouldn't be public, use GitHub's **private vulnerability reporting** on the Security tab instead. Please include reproduction steps and what an attacker would gain.

Since this is a local tool with no server component, there is no deployed instance to patch — fixes ship as a normal release and users update by pulling.
