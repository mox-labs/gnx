---
name: dao
description: "Sets a project up as a dao, a directed agent organization: reads the project, asks the owner a few questions, and writes six short documents (a constitution, governing policies, operating principles, positions, participants and a front-door README) into the repository, then checks them with the dao command and reviews what they say. This skill should be used when the user asks to \"set up a dao\", \"set this project up as a dao\", \"set up an agent organization\", \"set up the harness for this project\", \"write a constitution for this project\", \"define positions for our agents\", \"audit our dao\", \"check our agent setup\", or runs dao check. Not for doing work inside an organization that is already set up."
metadata:
  version: "0.2.0"
---

# dao 0.2.0: set a project up as a directed agent organization

## Why this exists

You start every session cold. You do not remember what this project decided last week, who
may publish a release, or whether the agent that last touched a file was allowed to. People in
an organization pick those things up by being around each other. You cannot. So you guess, or
you ask the owner something they have answered five times, or you work carefully and still
break a rule nobody wrote down.

A **dao** is the answer: the project's organization, written into its repository. Who is in
it, what each position does and refuses, who decides what, and what never happens here. With
it you can act with confidence inside your position, know at once when a decision is not
yours, and hand over work that someone else is set up to check. The owner can see how their
project is run, change it on purpose, and trust agents with more, because the limits are in
plain sight. That is the difference between a capable stranger and a collaborator.

Three beliefs shape everything below:

- **Direction stays with a person.** Agents are free in how they work and answer to a named
  human for what they do. Autonomy over method, never over accountability.
- **What is not written does not exist for an agent.** A rule in someone's head binds no
  session. So the organization is written down, short enough to be read every time.
- **Trust comes from being seen.** The organization lives in the open, in plain Markdown, and
  its git history is the record of how every rule came to be. Anyone can read how the
  project is run, and how to challenge it.

Your job with this skill is to help an owner say how their project is run, clearly enough
that the next session, which may be you, can pick it up and do good work.

## The model

An organization is described in the terms Ostrom used for institutions that last:

| Term | Means | Document |
|---|---|---|
| **Position** | a role: what it does, what it must not do, who checks its work | `dao/positions.md` |
| **Participant** | a person or an agent that holds a position | `dao/participants.md` |
| **Constitution** | what the organization is for, what it commits to, who may change its rules | `dao/constitution.md` |
| **Governing policies** | who decides what, how work is checked, how a rule is changed | `dao/policies.md` |
| **Operating principles** | how day-to-day work is done | `dao/principles.md` |
| **Front door** | the page that explains all of it to a stranger | `dao/README.md` |

Positions are separate from participants because they change at different speeds: a reviewer
position outlives whichever agent or person holds it this month.

The three rule documents are three levels, and the levels are what keep an organization from
quietly rewriting itself. Principles govern the work. Policies govern how principles and
policies change. The constitution governs who may change anything, itself included. So a
change is always made from the level above the thing changed, by whoever that level names.

## The four steps

### 1. Read

Read what the project already says about itself before asking anything: the README, the agent
instructions (`CLAUDE.md`, `AGENTS.md`), `.claude/agents/` and `.claude/skills/`, the
contributing and security files, the CI workflows, and the recent history. If the owner points
at documents from a wider organization, such as a handbook or house rules, read those too.

Sort what you find into what the project does (a rule that is enforced or habitually kept) and
what it only says. Write the constitution from the first list. An owner's time is the scarce
thing here, and every question you answer by reading is one you do not spend it on.

### 2. Ask

Ask the owner only what reading could not tell you, a few questions at a time. These five
carry the setup:

1. **What is this project for, and for whom?** In their words. Everything else is judged
   against it.
2. **Who is accountable for it?** A named person. Without one there is nobody to direct the
   organization, and nothing to set up.
3. **What must never happen here?** These become the commitments, the lines you and every
   other participant hold even when a task would be easier without them.
4. **What may an agent do without asking, and what always comes to you?** Whatever they
   answer, two kinds of decision go to a person: one that cannot be undone, and one being made
   here for the first time. Say so. Those are where an agent's confidence is least earned.
5. **What has already gone wrong?** Each answer is a policy or a principle that has paid for
   itself. Where nothing has gone wrong, write less: a rule nobody needed costs attention on
   every read and teaches nothing.

Then propose the positions: the smallest set that covers the work the project repeats, usually
two to four, the owner's among them. Agree the set before writing.

### 3. Write

Write the six documents in the formats of `references/documents.md`. Show the owner the
constitution first and get their agreement before the rest. Every other document is written
under it, and it has to be theirs for the organization to be directed by them.

Three things decide whether what you write is any use to the session that reads it next:

- **Write a position's remit as prose**: what it does, how, and what it refuses, concretely
  enough to act on. A one-word role gives the reader nothing to work from. Measured across 72
  agents, a described role beat a bare label by about half a point, and the role's name
  made no measurable difference (`references/findings.md`).
- **Give every position a checker that is a different position.** Work that only its author
  has read is the commonest way an agent's mistake ships. A position that only a person holds
  may end the chain: accountability stops with them.
- **Say what kind of check each check is**: a different model family, the same family reading
  cold, or a person. A reviewer from the same model family is a cold re-read. That is a good
  check, and it should be called what it is, so the owner knows what they are relying on.

Keep each document to about a page. The organization grows when a real situation shows a gap,
and a short document gets read at the start of every session.

Then make it a harness, so the documents are used and not just kept:

- **Point the agents at it.** Add a line to `CLAUDE.md` (or `AGENTS.md`) that names `dao/` and
  says to read `dao/README.md` at the start of a session. This line is what makes the
  organization exist for an agent at all.
- **Define the agent participants.** For each position an agent holds as a subagent, write
  `.claude/agents/<name>.md` with that position's remit and refusals as its prompt, and name
  the file in the participants table. A position the session itself holds needs no file.
- **Offer the check in CI.** A step that runs `dao check .` keeps the documents agreeing with
  each other as they change. The owner decides whether to add it.

### 4. Check

Run `dao check <project>`. It reports each document as PRESENT, PARTIAL or ABSENT and exits 1
unless all six are PRESENT. PARTIAL is the verdict to read closely: the file is there and
something in it does not hold, and the report names it. Fix and run again.

`dao check` reads form: sections, fields, and whether the documents agree with each other. It
cannot tell whether a constitution says anything. You can. So once it passes, read the six
documents as a stranger would and bring the owner what you find:

- Could two careful readers disagree about who decides something that has already come up?
- Does a position's remit cover almost anything, or do two positions cover the same work?
- Is there a commitment the project does not keep today?
- Does a principle only ask an agent to reflect on its own output? A habit earns its place
  when it brings in something the agent could not make up: a test run, a second reader, a
  person.
- Would a stranger reading only `dao/README.md` know how to raise a problem, and with whom?

Bring these as findings. They are the owner's decisions, and deciding them is how the
organization becomes theirs.

## Changing an organization that exists

Find the level of the change first. A new habit is a principle. A change to who decides, or
to how work is checked, is a policy. A change to the purpose, a commitment or the amendment
rule is constitutional. Read the document one level up to see who may make it, make the
change in a commit that says what prompted it, and run `dao check`.

When you hold a position and think a policy or the constitution is wrong, propose the change
to the position the documents name. Do not make it yourself, even when you are sure. The
levels only protect the project if the participants who could bypass them choose not to.

## When not to use this

- To do the project's work. Once the documents exist, the project's own instructions point at
  them, and you work from those.
- To produce the six files without the conversation. The value is in what the owner decided,
  and files nobody decided read as an organization while being none.

## The command

The plugin carries this text; the `dao` command is a separate Python package. Check with
`dao --version`, and install with:

```
uv tool install "git+https://github.com/mox-labs/gnx@dao-v0.2.0#subdirectory=components/capabilities/dao"
```

If `dao` is missing, say so and carry on: the documents can be written without it, and the
check is then owed.

| Command | Does |
|---|---|
| `dao check <project>` | audits the six documents; exits 1 unless all are PRESENT |
| `dao check <project> -v` | adds what each document with a gap must contain, and why |
| `dao check <project> --dir <path>` | when the documents are not in `dao/` |
| `dao spec --findings` | the six documents, what each must contain, the finding behind it |
| `dao policy` | the audit's thresholds, as a declared policy |

There is no `dao init`, on purpose: the writing is yours and the owner's, and the checking is
the command's.

## References

- **`references/documents.md`**: the format of each document as `dao check` reads it, with a
  small worked organization. Read it before writing.
- **`references/findings.md`**: what was measured behind the requirements, and where the
  measurements stop. Read it when an owner asks why a requirement is there.
