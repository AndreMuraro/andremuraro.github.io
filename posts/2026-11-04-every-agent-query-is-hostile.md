---
title: "Every Agent Query Is Hostile (Especially the Polite Ones)"
date: 2026-11-04
summary: "I treated every agent-written SQL query as hostile, then asked two AI agents to break my warehouse server. The result is an allow-list, masking inside the SQL, and one simple rule for ambiguity: refuse."
tags: [MCP, SQL, security, sqlglot, data governance]
---

An AI agent with a SQL tool looks like a very keen analyst. It can learn what a table means, inspect lineage, check freshness, and then helpfully write the query for you. What could possibly go wrong?

Here’s the catch. The agent also reads table descriptions, sample rows and documents, and any of them can contain instructions that didn't come from you. Prompt injection turns that cheerful little SQL box into a remote control.

So when I built [warehouse-context-mcp](https://github.com/AndreMuraro/warehouse-context-mcp), I adopted one rule: **every query is treated as written by an attacker.** Not because the agent is malicious, but because I can't tell, by looking at the SQL, whose idea it was. My first design was wrong, and two separate AI agents, each instructed to break the server, showed me where.

The project is an open-source MCP server. MCP, short for Model Context Protocol, is a standard way to expose tools to AI agents. This one provides warehouse context and read-only SQL across Snowflake, Databricks and DuckDB. The difficult part isn't running a query. It's letting an agent query tables containing PII, meaning personal data such as an email address, without letting that data escape.

## My denylist was a list of my own limitations

Version 1 did what most of us reach for first. It parsed the query, refused anything that wasn't a `SELECT`, blocked functions I considered dangerous, and masked PII in the results by column name.

This feels responsible right up until somebody wears a different hat.

Imagine an archive protected by a list of known troublemakers. A banned visitor returns in a new hat, which is a column alias. Another visitor asks for the furnace room instead of a manuscript, which is a table function reading a file. A third promises not to read a sealed letter, then asks whether its first line begins with A, which is a counting oracle. The archive keeps adding rules, while the visitors keep discovering that the building contains more than the rule writer remembered.

My first adversarial review was run by an AI agent instructed to break the server. It got through in more ways than I expected:

```sql
-- A table used as a value: the whole row, PII included, comes back as one struct.
select t from dim_charterers t limit 2

-- Renaming columns in the FROM clause hides which one is PII.
select * from dim_charterers as t(a, b, c, d) limit 2

-- A union puts PII under harmless column names.
select 1 a, 'x' b, 'y' c, 'z' d, now() e union all select * from dim_charterers

-- Nothing is returned, but the count answers a yes/no question about the data.
select count(case when contact_email like 'a%' then 1 end) from dim_charterers

-- Not PII, but not data either: files and environment variables.
select * from read_text('/etc/hostname')
select getenv('HOME') h
```

The counting query is especially rude. It returns no email address. It returns an answer to a yes-or-no question, and the agent can keep asking questions. A mask doesn't help much when the data can be reconstructed through the size of a count.

Then there were DuckDB functions for reading files and environment variables, plus enormous results such as `repeat('x', 500000000)`. A denylist blocks what its author already knows. Engines keep shipping functions, syntax and clever ways to spell the same thing. My memory is not a security boundary.

## Back to the SQL, with a shorter list

The rewrite flips the question. Instead of asking, “Is this known to be bad?”, it asks, “Is this known to be allowed?”

The policy parses SQL with [sqlglot](https://github.com/tobymao/sqlglot) into a syntax tree, which is the structured representation of the query rather than a pile of text. It accepts exactly one `SELECT`, or a set operation of `SELECT`s. Every node type and every function must appear on a reviewed list. Tables must be plain tables inside the configured schema.

Every column must also resolve against the live schema, through `*`, aliases, common table expressions and subqueries. That last part matters. If the server cannot establish which real table column a name refers to, it doesn't make an educated guess. It refuses the query.

Unknown syntax now fails closed. Table functions, `LATERAL`, `PIVOT`, recursive queries and vendor-specific functions don't each need a new entry on a growing list of villains. If they're not on the allow-list, they're already refused.

The refusals are intended to be useful to the agent, not just a locked door:

```text
select t from dim_charterers t
  -> forbidden construct: TABLECOLUMN

select * from read_text('/etc/hostname')
  -> only plain table references are allowed
```

This is less permissive than a denylist. That's the point. A query tool for agents should support a deliberately boring dialect of SQL, not every trick the warehouse learned in its latest release.

## Mask before the value goes anywhere

My first version masked PII after running the query. That meant the real value left the warehouse, crossed the network and sat in server memory before being replaced. Masking by output column name was also exactly what the alias and `UNION` attacks defeated.

I had built a careful shredder and installed it on the wrong side of the mailroom.

The rewrite changes the SQL itself. A PII column in the select list becomes a literal before the warehouse executes anything:

```sql
-- What the agent sent
select charterer_name, contact_email from dim_charterers limit 2

-- What the warehouse runs
SELECT "dim_charterers"."charterer_name" AS "charterer_name",
       '***' AS "contact_email"
FROM "main"."dim_charterers" AS "dim_charterers"
LIMIT 2
```

The email value is never read, so nothing downstream can accidentally log it, serialize it or forget to mask it.

Selecting is only one way to learn a value, though. A filter, sort, grouping, join or function argument can answer questions about it one bit at a time. PII is therefore refused in all those places. The only exception is a bare `COUNT(col)` or `COUNT(DISTINCT col)`, which supports useful questions such as how many distinct emails exist without returning the emails themselves.

```sql
select charterer_id from dim_charterers where contact_email like 'a%'
```

The refusal is explicit:

```text
column 'contact_email' is marked PII; it can be selected (masked) or counted with COUNT(col) / COUNT(DISTINCT col), nothing else
```

Call this the **Never Read Rule**: if a value must not leak, design the query so the value is never read.

## One name, two perfectly reasonable meanings

The second adversarial review used another AI agent, this time against the new design. It found one gap, and it was a good one:

```sql
select charterer_id, 1 as contact_email from dim_charterers order by contact_email desc
```

The output column named `contact_email` is just the constant `1`. My policy read the `ORDER BY` name as that harmless output alias. The engine read it as the real table column and sorted by the email address.

The mask still worked. The row order didn't.

A few queries using that difference could reconstruct a value letter by letter. Nothing sensitive appeared in a result cell, which made this much more interesting than another missing function on a blocklist.

The fix was to stop deciding which interpretation was probably right. An `ORDER BY` name that is both an output alias and a source-table column is refused:

```text
'"contact_email"' does not resolve to a table column, or is an output name in ORDER BY that is also a table column; rename the output
```

That gives me the rule I wish I had started with: **where the parser and the engine could disagree, refuse.**

## A guardrail needs a building around it

The SQL policy is one layer, not a force field. The rest of the setup assumes that some layer will eventually behave differently from what I expected:

- **Connections:** DuckDB opens read-only, with external access, extension installation and configuration changes disabled. On Snowflake, secondary roles are dropped and admin roles are refused at startup. On Databricks, the principal should have only `USE` and `SELECT` grants.
- **Execution:** every query gets a row limit and a timeout. Engine error messages are hidden for tables with PII, because an error message can quote the very value the policy is protecting.
- **Results:** per-value and total byte limits keep a query using `repeat('x', 500000000)` from turning the server into an expensive way to manufacture the letter x.
- **Metadata:** if the server cannot read the warehouse’s PII tags, it refuses to start. Missing tags must not quietly produce a system in which nothing is marked private.

For real PII, I would still put warehouse-native masking underneath all of this, such as Snowflake masking policies or Unity Catalog column masks. The server provides defense in depth, not an excuse to weaken warehouse permissions.

Every attack query from both reviews now lives in the repository as a regression suite and runs on every commit. The DuckDB attacks run end to end; the Snowflake and Databricks attacks run through the policy for their dialect. These tests are also the best documentation of why apparently fussy rules exist. “Refuse ambiguous output names” sounds optional until the query that motivated it is sitting beside it.

You can try the server with `uvx warehouse-context-mcp --demo`. The demo uses synthetic data, including `dim_charterers.contact_email`, and the [threat model](https://github.com/AndreMuraro/warehouse-context-mcp/blob/main/docs/threat-model.md) says what the server doesn't protect against.

I still want agents querying warehouses. I just don't want their safety to depend on my remembering every bad idea first. The archive doesn't need a longer list of suspicious hats. It needs fewer kinds of requests, masking before the page crosses the desk, and a standing answer for anything ambiguous: no.
