const contextDatabase = process.env.TEAM_CONTEXT_MONGODB_DATABASE;
const runtimeDatabase = process.env.AGENT_RUNTIME_MONGODB_DATABASE;

if (contextDatabase === runtimeDatabase) {
  throw new Error("Context and runtime MongoDB databases must differ");
}

db.getSiblingDB(contextDatabase).createUser({
  user: process.env.TEAM_CONTEXT_MONGODB_USERNAME,
  pwd: process.env.TEAM_CONTEXT_MONGODB_PASSWORD,
  roles: [
    { role: "readWrite", db: contextDatabase },
    { role: "dbAdmin", db: contextDatabase },
  ],
});

db.getSiblingDB(runtimeDatabase).createUser({
  user: process.env.AGENT_RUNTIME_MONGODB_USERNAME,
  pwd: process.env.AGENT_RUNTIME_MONGODB_PASSWORD,
  roles: [
    { role: "readWrite", db: runtimeDatabase },
    { role: "dbAdmin", db: runtimeDatabase },
  ],
});
