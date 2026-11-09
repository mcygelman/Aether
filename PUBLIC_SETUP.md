# Aether v0.1 setup guide

**The easiest way to install is to ask an AI assistant:** unzip the package, then tell your assistant to follow
`INSTALL_WITH_AI.md` in that folder. You only type your own password and API key, on Aether's welcome screen.

Sections 1–3 and 9–12 describe this package. Sections 4–8 still describe the earlier screens in places; the current
navigation is Chat, Missions, Roadmaps, Sprints, Staff, Teach and Creations, and those sections will be brought up to date.

This guide covers installing Aether on Windows, connecting a model, adding agents, running a project, and backing up your data. The [README](README.md) has the short version.

Menu paths are written as **Menu → Item**, using the labels in the top navigation bar.

## Contents

1. [Install and start](#1-install-and-start)
2. [First account and settings](#2-first-account-and-settings)
3. [Connect a model](#3-connect-a-model)
4. [Add agents](#4-add-agents)
5. [Let this computer host the team](#5-let-this-computer-host-the-team)
6. [Run a project](#6-run-a-project)
7. [Chat, Files and Creations](#7-chat-files-and-creations)
8. [Optional data sources (MCP)](#8-optional-data-sources-mcp)
9. [Where your data lives](#9-where-your-data-lives)
10. [Backup and new-root restore](#10-backup-and-new-root-restore)
11. [Uninstall](#11-uninstall)
12. [Accounts, roles and keys reference](#12-accounts-roles-and-keys-reference)

## 1. Install and start

### Quick install

1. Extract the whole Windows ZIP into a new folder with a short path. Python is bundled; you do not need a system Python.
2. Double-click `Install.cmd`. It prints the installed application folder and your data folder, and opens the installed folder.
3. In the installed folder, double-click `Aether.cmd`.
4. Keep that console window open. Open the browser address it prints.

The installed app uses port 8484 when it is free, and another free local port otherwise. Always use the printed address.

With no arguments, `Install.cmd` uses these default folders:

| Folder | Default location |
| --- | --- |
| Application | `%LOCALAPPDATA%\Programs\Aether\preview` |
| Data (called `<data>` in this guide) | `%USERPROFILE%\.a` |
| Credentials (encrypted API keys) | `%USERPROFILE%\.ac` |

The three folders must be separate; none may sit inside another.

### Install to folders you choose

From the extracted folder, in PowerShell:

```powershell
.\runtime\python.exe -I -B .\aether.py install --destination C:\av01\app --data-root C:\av01\data --credentials-root C:\av01\credentials --explicit-campaign-folders --json
C:\av01\app\runtime\python.exe -I -B C:\av01\app\aether.py serve --governed
```

- The paths are examples, not existing folders. Prefer the default location, or folders under your own user profile. Maintenance commands create a lock file next to these folders, so their parent folder must be writable.
- Keep the data path short. Without `--explicit-campaign-folders`, the installer refuses a data folder whose path is too long for Windows project folders and says so.
- `serve` requires `--governed`. `Aether.cmd` passes it for you; it adds no extra steps.
- To update, run the install again with the same three folders. Application files are replaced and your data is kept. An interrupted install is repaired the same way.
- Keep the extracted ZIP folder. You need it to uninstall (see [Uninstall](#11-uninstall)).

### Stop Aether

1. If this computer has joined an Aether network, open **Settings → Aether Networks** and select **Leave network**. Let any current agent turn finish.
2. Close the console window. This stops the server.

If Aether stops while this computer is still in a network (a closed console or a crash), it resumes hosting when the account that joined signs in again after the restart. It uses only that account's API keys. Only **Leave network** stops this.

### Run from the source ZIP instead

The source ZIP has a different layout and no bundled Python.

1. Install Python 3.10 with Jinja2 3.1.6, MarkupSafe 2.1.3, pypdf 6.21.2 and typing_extensions 4.15.0.
2. Change to `AetherAgents/LightweightAgents/Bridge`.
3. Put the extracted `AetherMemory` directory on `PYTHONPATH`.
4. Run:

```text
python -m aether_bridge.launcher start --edition atelier
```

The source launcher uses port 8484 by default. Stop it with `python -m aether_bridge.launcher stop`. Do not use the source launcher to stop an installed server.

## 2. First account and settings

1. Open the printed address and sign in with the default Admin account: username **admin**, password
   **aether-admin**. It is the same on every new install, so change it right away.
2. A one-time **welcome screen** opens. Type your own new password (at least 12 characters), choose your provider
   (DeepSeek, Claude or OpenAI) and, if you have one, paste its API key, then select **Finish setup**. Aether changes
   your password, saves the key encrypted, gives it to your account and adds that provider's connection. You can also skip the welcome and do these steps later from
   Settings, as described below. The welcome screen does not come back.
3. Later self-registered accounts start as Users. An install that already has accounts (an update, or data brought
   over from another copy) has no default Admin and no welcome screen.
4. **Settings → Overview** has a **Getting started** checklist that suggests four milestones: name this installation, connect a model, organize a project, and keep a useful result in Creations.
5. Select **Name installation** on the **This installation** card. The name can change later; its installation ID stays the same.

The checklist reads saved local records only. A saved model connection does not mean its server is running; check it on the Models page. Chat works without a project, so the milestones are suggestions, not requirements.

The Settings pages are: **Overview**, **API Keys**, **Models**, **Storage & Folders**, **Backup & Recovery**, **Aether Networks**, **Roles**, **Security** and **Agents**.

To add colleagues, see [Accounts, roles and keys reference](#12-accounts-roles-and-keys-reference).

## 3. Connect a model

Aether connects to an inference server you run, or to a hosted provider. It does not include or start llama.cpp, LM Studio or any model weights.

### Save an API key (hosted providers and protected servers)

1. Open **Settings → API Keys**.
2. Select **Create API Password**. Choose an API management password of at least 6 characters. It protects every saved key and is not stored.
3. Select **Add API key**. Give it a label, choose the provider, paste the key and select **Save key**.
4. Under **Assigned API keys**, grant usage to each account that will use the key. This includes your own Admin account: Admins also need an assignment.

Keys are encrypted with Windows user protection and your password, in the credential folder. No Windows environment variables are needed.

Assigned keys keep working after a restart and while management is locked. You need the management password only to change keys or assignments. More key controls are in the [reference](#12-accounts-roles-and-keys-reference).

### Hosted provider: DeepSeek, Claude or OpenAI

1. Save a key for that provider as above.
2. Open **Settings → Models** and select **Add connection**.
3. Choose **Hosted provider**, then **DeepSeek**, **Claude (Anthropic)** or **OpenAI**. Select your key by its label.

- **Claude** connects through Anthropic's OpenAI-compatible address (`https://api.anthropic.com/v1`). Anthropic
  describes that address as mainly for testing and comparing models; it has no prompt caching, so long projects cost
  more than they would through Claude's own API. Aether sends no temperature there, because Claude refuses one.
- **OpenAI**'s newest models take tool calls only through its newer Responses API, which Aether does not use yet.
  The preset uses GPT-5.5, which lists tool calls on the route Aether uses. Aether sends no temperature to OpenAI.
- Neither preset has been tried with a live key yet.
4. Review the server address, model and **Advanced: limits and budgets**.
5. Select **Save connection**.

Other OpenAI-compatible providers work the same way with a custom server address.

### This computer: LM Studio or llama.cpp server

LM Studio is the recommended starting point. Its [server guide](https://lmstudio.ai/docs/developer/core/server) explains the Developer tab and the **Start server** switch.

1. Start your inference server and load a model in it.
2. In **Settings → Models → Add connection**, choose **This computer**.
3. Select **Check for local servers**. Aether reads the model lists at `http://127.0.0.1:1234/v1` and `http://127.0.0.1:8080/v1`, without credentials.
4. Choose a responding server and one of its listed models.
5. Name the connection and select **Save connection**.

For any other port, enter the full API address and select **Load models from server**. The two checked addresses are common defaults, not a full scan. If no models are found, the page offers the LM Studio guide.

Use the model you have actually loaded, with its real address, model name and limits. Setting up a connection never loads or replaces a model.

### Another computer on your network

1. On that computer, turn on network serving in the inference server and allow its port through the firewall.
2. In **Settings → Models → Add connection**, choose **Another computer on my network**.
3. Enter its full API address, for example `http://192.168.1.42:1234/v1`, and select **Load models from server**.
4. Give the connection a name that identifies the machine and model, and save it.

There is no automatic network scan, and GPU memory is not pooled across computers.

### Things to know about connections

- Choose **No key** only when the server accepts unauthenticated requests. Local discovery never attaches a saved key.
- A saved key sent to another computer requires HTTPS.
- Saving checks the server's model list. It does not load or run a model. Existing connections offer **Check again**.
- A model listing does not prove context size, hardware capacity or tool-use support. The starter budgets are configuration values, not provider guarantees.
- Use a connection by selecting it in Chat, or by assigning it to an agent in a project's **Team** tab.
- Aether rereads the connections file after you change it. If a change is broken, agents keep using the last working version until you fix it.
- The Models page also has an advanced editor. Most of its extra settings belong to another edition and are not needed here. Older key references, such as an environment variable, remain available there to Admins only.
- **Optional helpers:** a connection saved with the exact name `ultralight` enables small Scout, Scribe and Checker helpers for project agents. They share the parent agent's call budget and have no tools or memory. Remove the connection to turn them off.

## 4. Add agents

Agents are managed in **Staff → AI** (also **Settings → Agents**). Creating or adding an agent never starts work and never calls a model. You give agents work by adding them to a project's team (see [Run a project](#6-run-a-project)).

Creating agents requires the **Manage Agents** permission.

### The demonstration team

The quickest start is the supplied three-agent team: a lead, an author and a reviewer.

- **In the app:** on **Staff → AI**, select **Create starter team**. It is shown until the starter team is registered. Aether creates the agents in a new `starter-team` folder inside the agents folder and registers them. Each gets its own identity and a fresh, empty memory. They use your first saved model connection, or none if you have not saved one yet.
- **From the command line:** from the installed folder, run:

```powershell
.\runtime\python.exe -I -B .\resources\AetherPublicFacing\Release\acceptance\runner.py prepare-minds --dest C:\av01\data\demo-homes --share C:\av01\data\atelier --deployment YOUR_SAVED_PROFILE_NAME
```

- `--dest` must be a new folder. `<data>\demo-homes` is one of the folders Aether searches for agents.
- `--share` is the network folder the agents will work in. For this computer's own network that is `<data>\atelier`.
- `--deployment` is the name of a saved model connection.
- To create a single agent that works alone, add `--roles lone_master`.

The command makes no model calls and copies no existing memory. Afterwards, register the new agents with **Add an existing agent folder**.

### Create a specialist

1. On **Staff → AI**, select **Create a specialist**. The specialist catalog opens.
2. Select one or more roles and choose **Add selected specialists**.
3. Choose a **Default model connection**, or **Configure later**, then select **Create fresh agents**.

Every catalog role is lightweight. Each new specialist gets a fresh identity and its own technical memory; nothing is copied from existing agents.

### The Agent Editor

**Staff → Agent Editor** creates custom lightweight templates, shared within this installation.

1. Create a new template or import a draft.
2. Set its project role, technical subject, supported tools and writable project folders.
3. Select **Save revision** (or **Save as new template**).
4. Pick an exact saved revision and select **Create specialist**. Choose a model connection or **Configure later**.

Saving a revision never changes existing agents. Text in a template does not grant tools or credentials. Users without Manage Agents can view and download saved revisions. Download unsaved drafts before you leave the page.

### Add an existing agent folder

1. On **Staff → AI**, select **Add an existing agent folder**.
2. Choose an agent found in your agent folders and select **Add agent**.
3. To look elsewhere, use **Set folder location** and add a folder. Aether searches it and one or two levels below.

Registration copies nothing and changes nothing in the folder.

### What else is on Staff → AI

- The **Type** filter separates **Lightweight** and **Heavyweight** agents. Every agent this edition creates is lightweight.
- **Create a heavyweight agent** and **Heavyweight merge** are marked **Future Feature**. They are not available in this edition.
- **Ultralight** templates can be drafted and exported in the Agent Editor, but this edition does not run them as custom agents.
- **Technical Learning** shows each specialist's technical lessons and how they change. Specialists write these themselves, with no review step. Project notes stay with the project.
- **Welcome Dashboards** and **Memory viewer** let you read an agent's memory and activity.
- **Make unavailable here** stops this computer from using an agent. Its folder, memory and project assignments are kept, and a turn in progress can finish. **Make available** brings it back. Each computer manages its own availability.

## 5. Let this computer host the team

Agents take their turns on a computer that has joined an **Aether network**. A network is a shared folder that holds missions, projects and their conversations. Computers that join it run agent turns as messages arrive.

### On one computer

You still "join a network", because joining is how a computer starts hosting agent turns. Your own computer's network is a folder in your data folder (`<data>\atelier`). Nothing leaves the computer.

1. Open **Settings → Aether Networks**.
2. Under **This computer**, choose this computer's own network in the **Network** list. It is listed as **This computer's network** until you rename it, marked "(on this computer)".
3. Select **Join network**.

While joined, agents take their turns by themselves as project messages arrive. Joining calls no model; agents work only when a project has messages for them, within its budget.

If you do not join, a project's Team panel shows **Ask to take a turn** for each agent, and agents work only when you ask.

**Leave network** lets a turn in progress finish first. Nothing is deleted.

### Add more computers

1. On the computer that keeps the network, share its network folder on your local network.
2. On each other computer, install Aether and open **Settings → Aether Networks**.
3. In **Network**, choose **Another network folder…**, type the shared address (for example `\\machine1\aether`) and select **Join network**.

**Computers in this network** lists each joined computer and whether it is online. **Options for this computer** sets this computer's name, any extra agent homes it hosts, and the model connections file for those agents. You can change these options only while the computer is out of the network.

Aether networks are separate from model connections. To use an inference server on another computer, add it in **Settings → Models**.

## 6. Run a project

A **mission** is a goal. Its **projects** hold the actual work. Each project has its own team, roadmap and conversation.

### Create a mission and a project

1. Choose **Missions → Add New** to name a mission and describe its purpose. **Missions → Manage** opens the Missions page.
2. On the mission's card, select **New project**. To create a project with no mission, use **New project** under **Unassigned projects**.
3. Choose the kind of work: a project for a team, or a question for the librarian.
4. Write the **Project brief**: what to deliver, why it matters, anything that helps, and the budget. Attach files if useful.
5. Select **Create project**.

If naming or mission assignment fails after the project is created, the project still opens with a notice so you can finish its details. Do not create it again.

### Set the budget

Write the budget in the brief as, for example, `Budget: 20 model calls` (plain forms such as "a budget of 20 calls" also work). One model call is one request to a model. When the budget is used up, the team stops and waits for you. To allow more, send a message such as `Budget: 40 model calls`.

If the brief names no budget, the project gets a default of 60 model calls, and Aether says so in the conversation. Write a budget line at any time to change it.

### Add the team

A new project starts with an empty team.

1. Open the project's **Team** tab. The project tabs are **Conversation**, **Roadmap**, **Team** and **Records**.
2. Under **Add an agent**, choose an available agent, its **Role in the project** (Lead, Team member, Reviewer or Librarian) and its **Driving model**.
3. Select **Add to project**.

**Edit assignment** changes an assignment; **Remove from project** unassigns the agent without deleting it. Saving an assignment starts nothing. A running agent finishes its current turn with its old settings, and the change applies on its next turn. Identity, memory, keys and other projects are not affected.

### Approve and accept

1. The lead replies to the brief with a roadmap: sprints, and tasks in each.
2. Read it, then select **Approve the plan** (or **Approve sprint** when only one sprint is waiting), or **Ask for changes**. One approval covers every sprint planned so far. A later plan asks only about the sprints it adds.
3. The team works through the plan. You can write to them at any time.
4. When the reviewer has written, review the result and select **Accept result** or **Ask for changes**.
5. After you accept, the agents write their closing notes. You do not need to wait for them before starting other work.

These buttons send the same words a person would type, so you can also write them yourself. The project page shows the stage (Planning, In progress, In review, Closing, Done) and the budget used.

## 7. Chat, Files and Creations

**Chat** talks to a model directly, with no agent and no agent memory. Choose a saved model connection you have access to. Chat history is private to your account.

**Files** (in Chat) gives the model access to one folder. It starts with your personal scratchpad.

- Browse to another folder, check what access is on, or turn file access off. The choice survives a restart.
- Ask the model to list the folder before choosing a file. Paths, including pasted absolute paths, must stay inside that folder.
- Turn on edit proposals only when needed, and review each change before applying it.

**Reading scanned documents and images.** Chat reads text from scanned PDF pages and from images using Windows OCR on this computer. No account, key or upload is involved.

- Requirements: Windows PowerShell and a Windows OCR language pack. Aether uses an installed preferred language, falling back to English, and tells you if support is missing.
- Images: PNG, JPEG, BMP, TIFF and WebP (WebP needs an installed Windows decoder). Up to 20 MiB, 40 megapixels and 30,000 pixels per side. Multi-page TIFFs are read in order, up to 500 pages; animated images use the first frame.
- PDFs: up to 40 pages per read within the time budget. Select a single page to read it first in a long scan.
- Whole documents have a 25-second / one-million-character budget. Unread pages are listed and can be read one at a time.
- The model receives recognized text only, not the image. This is not model vision: it cannot describe pictures or diagrams. OCR can misread text, numbers and layout.
- Originals are never changed. Refresh existing search indexes to include newly recognized text.

**Creations** is the shared showcase for useful results. In Chat, save your edits to an artifact, then choose **Save to Creations…** to review a copy before saving it. Your private original stays unchanged, and nothing is sent to agents. Admins can add Creations; the Manage Teach permission also allows Teach Frames.

## 8. Optional data sources (MCP)

Aether can query your organization's reporting services through MCP. A fresh installation has no data source and installs no service.

Data Sources is not a Settings tab. Open it from **Settings → Overview**: on the **Data Sources & Reports** card, select **Open Data Sources**.

### Set up a source (requires Manage Data Sources)

1. Give the source a name, an optional `@` mention alias, a purpose and a business guide.
2. Choose the connection: Streamable HTTP or a local stdio service. Remote HTTP endpoints require HTTPS and can use a bearer token.
3. Discover the service's tools, then select the reporting capabilities and the source roles that may use it.
4. Turn on model use only if source details and results may be sent to the Chat or agent models.

Database drivers and the restricted database accounts your DBA provides belong to the MCP service, not to Aether. SQL Server, Oracle, MySQL and PostgreSQL are listed as provider descriptions; no native drivers are bundled.

### Use a source

Users need the **Use Data Sources and Reports** permission and access through a source role. Chat finds authorized sources automatically; the source picker and optional `@Alias` mentions narrow the choice.

Open a returned report to see the actual rows, parameters, run time and completeness. Table filters change the view only; CSV and JSON exports include every received row. Saved report definitions are private and keep parameters and view settings; dated snapshots keep earlier results. A rerun checks current access and fetches fresh data.

### Limits

- The MCP service and the database accounts enforce permissions. A tool's "read-only" label is not a security boundary.
- Responses are limited to 4 MiB, 5,000 rows and 200 columns. Models see at most 50 rows.
- Partial or unknown completeness is always shown.
- Not included: interactive OAuth sign-in, merging paged results, and automatic service installation.
- Tool names and result shapes vary between services. Test the service and its result mapping before relying on it.

## 9. Where your data lives

`<data>` in this guide means the data folder chosen at install. With `Install.cmd` and no arguments it is `%USERPROFILE%\.a`. **Settings → Storage & Folders** shows its exact path and every other location, with links to the page that manages each one.

| Location | What it holds |
| --- | --- |
| `<data>\.aether_control\operators\accounts.sqlite3` | Accounts, roles and API key assignments |
| `<data>\b\runtime_profiles.json` | Model connections (the file shown on the Models page) |
| `<data>\minds` | The default agents folder; new agents are created here |
| `<data>\homes`, `<data>\demo-homes` | Also searched for existing agents; nothing in them is moved |
| `<data>\atelier` | This computer's own network folder: missions, projects and their conversations |
| Credentials folder (`%USERPROFILE%\.ac` by default) | Encrypted API keys, kept apart from data |

The application folder's `installation.json` records which data and credential folders it uses.

**Inside a network folder:** each project's conversation record is `daybooks\<project>\daybook.jsonl`. Attached files are stored once, by content hash. There are also `mailboxes\` and the project work folders `work\<project>\`.

**Inside an agent folder:** `participant.json`, the agent's identity and continuity files, and `memory\.cartridge.json`, which points to its memory file. That memory file may be stored elsewhere.

Network folders, extra agent homes and memory files can be outside `<data>`. Write down every location you choose.

Data and credential folders cannot be moved from the app. To move to new folders, use backup and restore.

## 10. Backup and new-root restore

Backups run offline with `Maintenance.cmd` in the installed application folder. Never copy an open memory file by hand.

### What a backup includes

Admins can open **Settings → Backup & Recovery** to preview this. `Maintenance.cmd coverage` prints the same preview offline. The preview is not a backup.

- **Included:** accounts, roles and key assignments; private Chats and scratchpads; custom templates and specialist creation records; registered agent folders, including ones outside the data folder; agent memories; project checkpoints and projects; data sources and saved reports; workspace records; model connections.
- **Left out:** the API key store, files named as credentials, data-source service tokens, and a shared network folder unless you add it.

Registered project folders and agent folders are added to the plan automatically, each as its exact folder.

Other folders outside `<data>`, such as an external network folder or memory files, must be added by hand. Give `prepare-backup` a `--mount share=<absolute share>` option, plus one `--mount id=<absolute path>` for each further folder. The folders must not overlap.

The Backup & Recovery page can check a backup folder and plan file for you. It then writes the exact Command Prompt steps, with every path quoted.

### Make a backup

1. On each computer that contributes to the network, open **Settings → Aether Networks** and select **Leave network**.
2. Wait for active turns to finish, then close Aether (close its console).
3. In Command Prompt, in the installed application folder, run:

```text
Maintenance.cmd prepare-backup --destination C:\av01-backup --plan C:\av01-plan.json
Maintenance.cmd backup --plan C:\av01-plan.json --plan-sha256 <hash returned by prepare-backup>
Maintenance.cmd verify-backup --archive C:\av01-backup --manifest-sha256 <returned manifest hash>
```

- Run one command at a time and continue only when it succeeds.
- Review the plan before `backup`. It lists private paths, so keep it private.
- Keep the manifest hash that `backup` returns. You need it to verify and restore.
- All paths must be absolute. The backup folder and plan file must be new, short and separate from your data.

### Restore to a new folder

```text
Maintenance.cmd restore --archive C:\av01-backup --manifest-sha256 <returned manifest hash> --target C:\av01-restored
Maintenance.cmd recovery-report --target C:\av01-restored
```

- Restore writes into a new folder and leaves the originals untouched. Only the same application build that made the backup can restore it.
- A complete restore reports `VERIFIED_STAGING_NOT_ACTIVE`: the copy is verified but nothing is switched over automatically.
- If the restore stops before it writes `restore.json`, keep the originals and restore again into another new folder.

`recovery-report` checks the restored folder again and changes nothing. It contacts no provider or service. You can also read it in **Settings → Backup & Recovery** under **Read a restored copy**. It lists:

- the restored folders, and saved references that still point to the original folders;
- specialist creation records, and whether repeating each is safe;
- account key assignments (key values are never in a backup);
- data sources whose service token must be re-entered in Data Sources;
- model connections;
- each memory file's owner and integrity seal, read without changes.

### Start using the restored copy

1. Open **Settings → Aether Networks**. In **Network**, choose **Another network folder…** and enter the restored network folder. Under **Options for this computer**, set the restored agent homes and the model connections file you intend to use.
2. Update agent memory pointers if needed. A relative pointer moves with its agent folder. An absolute pointer to a memory file outside the folder must be changed to the restored location.
3. Re-enter API keys and data-source tokens. A new computer needs its own credential setup.
4. Join the network when ready.

`Maintenance.txt` also lists `switch` and `replace-under-hold`, which replace original data in place. They are not the restore route for this version. If you do use `replace-under-hold`, Aether starts on the restored data and prints a note saying so; the replaced originals stay in the restore folder's `previous` folder.

## 11. Uninstall

1. Close Aether.
2. From the original extracted ZIP folder (not the installed folder, which cannot delete itself), run:

```text
Uninstall.cmd --destination C:\av01\app
```

With a default install, `Uninstall.cmd` with no arguments removes the default application folder.

Uninstall keeps your data and credential folders. Delete them yourself if you no longer need them.

## 12. Accounts, roles and keys reference

### Accounts

- Add colleagues in **Staff → Human → Add account**, choosing an initial password and role. You stay signed in.
- **Set new password** on another account ends that account's sessions; its access and history are kept. Use **Change password** for your own account.
- Disabling an account is reversible. Deleting one keeps its files; a new account with the same username does not inherit the old scratchpad or private Chats.
- At least one active Admin must remain. These screens cannot recover the only Admin if no Admin can sign in.
- Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes in SQLite. Older account JSON files are imported once and kept for recovery; SQLite is used afterwards. Roadmap trackers remain JSON.

### Roles and permissions

Define named roles in **Settings → Roles**, then assign roles or direct permissions in **Staff → Human** (**Edit access**). Permissions add together. Admins always have every management permission. Managers cannot grant access beyond their own.

| Permission | Allows |
| --- | --- |
| Manage Missions and Projects | Missions, projects and team assignments |
| Manage Agents | Creating and registering agents; saving Agent Editor templates |
| Manage Teach | Teach Frames, including in Creations |
| Manage Guides, Manage Dashboards | Shared guides and dashboard notes |
| Manage Settings | Naming the installation and configuring connections |
| Manage Accounts, Manage Roles | Accounts and roles |
| Manage API Keys | Adding, revealing, renaming, replacing and deleting keys |
| Assign API Keys | Granting key usage, without seeing the keys |
| Manage Data Sources | Configuring MCP sources |
| Use Data Sources and Reports | Querying sources the user's roles allow |

Ordinary Users have full Chat and view-only access elsewhere unless granted more.

### API keys

Three kinds of credential are separate: account passwords, the API management password, and provider API keys.

- **Unlock management** is needed to change keys or assignments. It applies only to your own session. **Lock management** closes it; assigned usage continues.
- **Suspend API use** stops new requests with assigned keys. A request already sent may finish. **Resume assigned API use** restores usage under current accounts and assignments.
- With Manage API Keys and management unlocked, saved keys offer **Reveal**, **Copy**, **Rename**, **Replace** and **Delete**. Labels must be unique. Changes affect later requests; a deleted key leaves its model connections needing another key.
- **Reset API password…** deletes all saved keys and clears their assignments, after you confirm. It does not revoke keys at the provider. Re-add keys, update connections and assign usage again afterwards.
- After upgrading an older installation, a key manager may need to unlock management once before assigned keys work. Installing or upgrading never creates key assignments.
- Backups and exports never carry the key store.

### Help and other references

**Help** in the navigation bar includes **Guides**, with **Admin setup**, **API Keys** and **First steps** walkthroughs, and **Emergency Halt**, which stops new model requests and agent starts.

See also [SECURITY.md](SECURITY.md), and the glossary in `docs/GLOSSARY.md` (in the source repository).

### Acceptance test kit

The installed folder includes an acceptance kit in `resources\AetherPublicFacing\Release\acceptance\`. Its README describes two test projects and a restart between them. Give agents only the prepared inputs; keep the evaluator and reference answers outside every folder an agent can reach. The result matrix starts with every check marked NOT RUN, for you to fill in.
