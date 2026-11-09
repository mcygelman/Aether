/* Owner: navigation help strings and page-to-help mappings.
 * Must never: render controls, fetch records or implement workflows.
 */
window.aetherShellParts=window.aetherShellParts||{};
window.aetherShellParts.helpData=(()=>{
  const HELP = {
    'data-sources': ['Data Sources connects Chat and project agents to your organization’s MCP reporting services, using your source roles.', 'Admins configure the service and select reporting tools. Database accounts and drivers belong to that service. Results show their origin and completeness; saved report definitions and dated snapshots are private to your account.'],
    'chat-home': ['Your private conversations and curated personal knowledge, separate from agent memory.', 'Save selected messages as knowledge or session summaries. Review claims before confirming them. Context shows what may be sent to the selected model. Archive excludes an item from future recall; exports are private files to share deliberately.'],
    storage: ['Storage & Folders shows the locations used by this installation and links to the controls that own each setting.', 'Installation roots are selected by the installer. This page does not move stores. Use Maintenance.cmd for backup and restore; external agent homes and network folders may need separate inclusion.'],
    recovery: ['Backup & Recovery shows Admins what a backup of this installation keeps, leaves out or still needs, and writes the exact offline Maintenance.cmd commands.', 'The preview is not a backup. Backups run with Mission Control closed. A restore goes to a new staging folder and is not active; its report lists what still names original folders and which keys must be reconnected; data-source service tokens are never in a backup and must be re-entered.'],
    campaigns: ['A mission is a goal you are working toward, with one or more projects carrying out the work. Each project keeps its own team, roadmap and conversation.',
      'Use Add New to name a mission and describe its purpose. Search and filter to find work. Deleting a mission keeps its projects under Unassigned projects, and Undo restores the mission.'],
    projects: ['A project is one piece of work a team delivers, such as “Mission Control GUI revamp”. You give it a short brief; its lead plans a roadmap of sprints, each with tasks for the team.',
      'Open a project to talk with its team, approve each sprint, and accept the result.'],
    unfiled: ['Projects that belong to no mission yet. Use the project menu’s “Move to mission” action to organize them.'],
    brief: ['The brief is the first message of a project: what to deliver, why it matters, anything that helps, and the budget (for example “Budget: 20 model calls”).',
      'The lead reads it and replies with a roadmap: one line per sprint, one line per task, with who does each task.'],
    'next-step': ['What the project needs from you now. A project moves through five stages:',
      ['Planning: the lead writes the roadmap.', 'In progress: you approve the plan once, and the team works through it at its own pace; a later plan asks only for the sprints it adds.', 'In review: the reviewer has written; you accept the result or say what to change.', 'Closing: the agents finish their closing notes.', 'Done: the project is finished.']],
    conversation: ['Every message, file and reply in this project, in order. Messages to everyone go to the whole team; the team reads all messages since its last turn.',
      'Files you attach are kept once, with a fingerprint, so you can always download the exact version.'],
    message: ['Write to the team at any time: questions, changes, extra material. Ctrl+Enter sends.',
      '“Approve the plan” (or “Approve sprint”) and “Accept result” send the same words a person would type, so you can also simply write them.'],
    team: ['The agents working on this project and their roles: the Lead plans and coordinates, Developers do the tasks, the Reviewer checks the result.',
      'If this computer has joined a network, agents take their turns by themselves as messages arrive. Otherwise you can ask one to take a turn now.'],
    usage: ['How much of the budget the project has used. A model call is one request to a model; the budget in the brief caps it.'],
    chat: ['Choose a model and explore ideas. Chat history is saved separately from agent identities and memory.',
      'Files starts with your personal scratchpad. Choose a folder or turn access off; the setting survives restart. The model can list and read attached files. Enable edit proposals and review each before applying. Transform selected messages or the whole conversation into an editable artifact. To Aether translates into the symbolic language; Teach Frame makes a separate teaching draft. Save edits, then use Save to Creations to review a workspace copy, or export the result. Admins can add Creations; Manage Teach also allows Teach Frames.'],
    roadmap: ['The roadmap is the lead’s plan for this project: its sprints, in order, and the tasks in each, with who does them.',
      'A sprint starts when you approve it. When every sprint is approved and the reviewer has written, you accept the result.'],
    records: ['Every message and file of this project, each with its fingerprint (SHA-256), so anyone can check that nothing changed.'],
    network: ['Your Aether network is the set of computers that host agents and run their turns for your projects. Model connections to LAN inference servers are configured separately in Settings → Models.',
      'Computers in a network use a shared workplace folder for missions and projects.'],
    'this-computer': ['Choose a network and join it. While this computer is in the network, agents take their turns as messages arrive. Leaving lets a turn already in progress finish first; nothing is deleted.',
      'No model is called by joining: agents work only when a project has messages for them, within its budget.'],
    computers: ['Every computer that has joined this network. Online means it answered in the last few seconds.'],
    'add-computers': ['A network lives in one shared folder. To add another computer, share that folder on your home network, then join it from the other computer by typing its address (for example \\\\machine1\\aether).'],
    'network-options': ['Optional settings for this computer: its name in the network, additional agent homes it hosts, and the model connections file those agents use. These homes are separate from the discovery search folders managed in Agents → Set folder location.'],
    agents: ['Create a specialist opens the shipped catalog; Add an existing agent folder registers a home you already have. Neither action starts project work.',
      'Technical Learning shows specialists’ technical lessons and evolving paths. Project working notes stay with their project. Authored personality remains separate.',
      'Assign an available agent to a project with its role and model. Making it unavailable here preserves its folder, memory and assignments.'],
    insights: ['Read an agent’s memory and activity. Specialist technical cartridges open through Technical Learning; their project context is stored separately.'],
    keys: ['Unlock management to change or assign saved API keys. Assigned usage survives restart and continues while management is locked; the management password is not saved.',
      'Even Admin needs a usage assignment. Suspend API use stops subsequent assigned-key requests; an already sent request may finish.'],
    models: ['A model connection tells Aether how to reach a model: its server address, model name and optional API key. Saving keeps the connection, then attempts a connection check. A failed check does not undo the save; use Check again after fixing the server or settings. A model listing does not establish context capacity or inference readiness.'],
    roster: ['Choose who works on this project, their role and the model connection that drives each one. Removing someone from a project never touches their memory or other projects.'],
    'roadmap-status': ['Where the project stands, read from its own records: stage, what it waits on, its team and its budget. The records stay below for checking.']};
  HELP.sprints = ['Every sprint in your roadmaps, grouped as Waiting for you, Working now, Planned and Finished.',
    'Open a sprint to follow its tasks and outputs, or to review them. Start sprint on a planned sprint opens the New sprint form with its name and brief filled in.'];
  HELP.teach = ['From the School shows the frames the AI staff are taught; read one, or make a workspace copy to adapt it.', 'Create a teaching draft here or transform a conversation in Chat. Edit, tag and export the result when it is ready to share.',
    'Saving a Teach Frame does not automatically write to an agent’s memory.'];
  HELP.creations = ['Keep summaries, task lists, symbolic translations, HTML pages, SVGs and other artifacts together. Use the filters to find material and open it to read or export. Admins manage ordinary Creations; Manage Teach also permits Teach Frames.', 'In Chat, save artifact edits and use Save to Creations to review a separate workspace copy. Saving that copy makes it visible to signed-in operators here; it does not change the private Chat artifact or send it to agents.'];
  HELP.settings = ['This overview reads local setup status without contacting providers or changing connections. Open a settings page to manage its records. Saved models are not a live connection check; unavailable status means a source could not be read.'];
  HELP.humans = ['Human operators sign in with a local SQLite account. Passwords are salted hashes. The first account is an Admin; later self-registered accounts start as Users. Authorized managers add accounts, roles and key assignments.', 'Admins can set a new password for another account, ending its current sessions. Use Change password for your own account. Account passwords and the API vault are separate.'];
  HELP.roles = ['Named roles combine selected permissions; memberships and direct grants add together. Admin management permissions are always included. Delegated managers can grant only access they may manage, and at least one active Admin must remain.', 'API-key usage is assigned separately, including for Admin. Use Assigned API keys in Human Staff or API Keys.'];
  HELP['technical-learning'] = ['Inspect specialist-owned technical lessons, revisions and evolving paths. Specialists file and revise directly without a human-review or score gate; project-specific notes stay with the project.'];
  HELP.dashboard = ['These counts come from current workspace records. Open the related area to work with the underlying projects or artifacts.'];
  HELP.security = ['Lock management closes secret management without stopping assigned key usage. Suspend API use is the separate control for stopping subsequent assigned-key requests; already-sent requests may finish.'];
  HELP.roadmaps = ['Open roadmaps are shown by default. Search projects and missions, choose a mission, or switch to Closed or All to find earlier work.',
    'View plan shows the recorded sprints, tasks and assigned people. Sprint approvals are not a measure of task completion. Projects with no plan yet appear under Awaiting a roadmap.'];
  HELP.missions = HELP.campaigns;
  const PAGE_HELP = {
    '/agents': [['h1', 'agents']], '/agents/insights': [['h1', 'insights']],
    '/settings/api-keys/': [['h1', 'keys']], '/settings/models-context/': [['h1', 'models']],
    '/projects/roster': [['h1', 'roster']], '/projects/campaign': [['h1', 'roadmap-status']],
    '/projects/campaign-receipts': [['h1', 'records']]};
return {HELP,PAGE_HELP};
})();
