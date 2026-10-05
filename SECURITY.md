# Security policy

Do not deploy the development bearer authenticator or local Docker executor to production. Production adopters must provide workload identity, short-lived credentials, network restrictions, secret redaction, and an execution adapter appropriate to their platform.

Report suspected vulnerabilities privately to the repository owner. Do not include credentials, proprietary documents, or exploit payloads containing real organizational data.

The model is never an authorization boundary. All retrieval, tools, supplemental-memory mutation,
repository access, publication, and automation limits must be checked in application or execution
code. No application or model operation can promote memory into canonical knowledge.
