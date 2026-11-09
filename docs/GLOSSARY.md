# Glossary

The words on Aether's screens first, then the words you will meet in the code and the record.

## On the screens

| Term | Meaning |
|---|---|
| **Mission** | An optional grouping of related projects, with a name and a purpose. |
| **Project** | One piece of work with a brief, a budget and a team. In the code and the record a project is a *commission* or *campaign*. |
| **Brief** | What you want delivered, why, what helps, and the budget ("Budget: 60 model calls"). It is the first note of the project and its authorization. |
| **Roadmap** | The lead's plan for a project: its sprints and tasks. **Roadmaps** in the navigation lists them across projects. |
| **Sprint** | One stage of a plan. You approve the plan once; a sprint added later asks for approval once more. |
| **Approve the plan** | Your one approval for the sprints planned so far. The work does not start without it. |
| **Accept result** | Your decision that the delivered work is done. It closes the project and asks each agent for its closing notes. |
| **Staff** | The people (**Human**) and agents (**AI**) registered in this installation. |
| **Lead, developer, reviewer** | The roles an agent takes in a project's **Team** tab. The lead plans and hands over, the developer builds, the reviewer checks the work independently. The Team tab calls the developer role **Team member**, and the conversation shows it as Developer. In the code: lead, member and reviewer. |
| **Agent** | An AI team member with its own home folder and memory. The code also calls it a *mind* or *participant*. |
| **Memory viewer** | A read-only view of one agent's memory: what it filed, how memories link, and which ones are fading. |
| **Welcome Dashboard** | What an agent sees about itself when it wakes: its role, recent work and the headers of its memories. |
| **Teach / Teach Frames** | Reusable guidance (patterns, procedures) that can be lent to a project instead of being pasted into every prompt. |
| **Creations** | A shared showcase of artifacts saved from Chat or projects. |
| **Chat** | A private conversation with a model, with optional access to a folder you choose. It needs no project. |
| **Aether Networks** | The shared folder through which one or more computers host a team. **Join network** lets this computer take agent turns; **Leave network** stops it after the turn in progress. |
| **API Keys** | The encrypted store for provider keys, with its own management password, and the grants that let accounts use them. |
| **Models** | Model connections: where a model runs (a hosted provider, this computer, or another computer on your network) and its budgets. |
| **Lightweight agent** | The agents you create in this edition: a specialist made from a role template or the Agent Editor, with its own home and memory. |
| **Heavyweight agent** | A long-lived agent created through a consent conversation with its own terms. Creating or merging heavyweights belongs to the private development edition; on the Staff → AI page those actions are marked **Future Feature**. |
| **Ultralight helpers** | Scout, Scribe and Checker: small-model helpers an agent can hand excerpts to. They return drafts, share the parent's call budget and get no tools of their own. They are enabled by saving a small-model connection named exactly `ultralight` (Settings → Overview explains this). |
| **Technical Learning** | On a specialist's card in Staff → AI: the technical lessons it has filed for itself, their revisions and the subjects they cover. Specialists file these directly; no review step is needed. |
| **Future Feature** | A control shown for orientation that does nothing in this edition. |

## In the code and the record

| Term | Meaning |
|---|---|
| **Atelier** | The part of Aether that runs projects as conversations among agents (`aether/atelier/`). |
| **Daybook** | The project's record: one JSON line per note, each chained to the previous by hash, with delivered files kept by hash (`daybooks/<project>/daybook.jsonl` on the network folder). |
| **Facts** | What the Atelier reads out of the daybook after every note: the plan, the team, approvals, deliverables and verdicts (`aether/atelier/facts.py`). |
| **Turn** | One agent waking, reading its unread notes, using its tools and writing a reply (`aether/atelier/turn.py`). |
| **Place** | The background worker on one computer that wakes the agents whose homes live there (`aether/atelier/place.py`). |
| **Share** | The network folder that holds daybooks, mailboxes and project work folders. |
| **Home** | An agent's folder: `participant.json`, its identity, its continuity and a pointer to its memory cartridge. |
| **Cartridge** | The agent's memory file, a single SQLite database managed by the memory engine (`AetherMemory/memory_fabric/`). |
| **Closing notes** | What an agent files into its own memory when a project is accepted: what it did, what it learned, what to carry and what to skip. |
| **Library, librarian** | A collection of reference material on the share and the agent that keeps it, lending items to projects that need them. |
| **The three numbers** | How the process is measured: thinking per minute of overhead, refusals per hundred model calls, and approvals per sprint. |
| **Four stops** | The only reasons the Atelier halts work: money (the budget), consent, secrets and harm. |
| **Edition** | The Atelier edition is this public application. A private full edition with more governance tooling exists; its routes answer 404 here. |
| **Mission Control** | The application's web server and shell (`aether/mission_control/`). The product name on screen is Aether Mission Control. |
