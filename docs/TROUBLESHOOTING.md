# Troubleshooting

| What you see | What to do |
|---|---|
| `Aether.cmd` prints an address other than `:8484` | Normal. Another program (or another Aether) is using 8484, so the app chose a free port. Use the printed address. |
| **Check connection** fails for DeepSeek | Open **Settings → API Keys**, check the key is saved and assigned to your account, then **Check again** on the Models page. A model listing does not run the model. |
| **Check for local servers** finds nothing | Start your local server first. It checks `127.0.0.1:1234` (LM Studio) and `127.0.0.1:8080` (a common llama.cpp port). For another port, type the full address ending in `/v1` and select **Load models from server**. |
| A project sits at **Planning** and nothing happens | This computer is not hosting the team. Open **Settings → Aether Networks**, choose the network folder and **Join network**. Also check each agent in the project's **Team** tab has a model. |
| The page asks you to approve | The lead has planned sprints. Read the roadmap, then **Approve the plan**. |
| The work stopped with a budget message | The project reached its call budget (the brief's, or the default of 60 calls when the brief named none). Write a note with a new total ("Budget: 100 model calls.") to let it continue, or accept what was delivered. |
| An agent's turn failed with a credential error | The account that joined the network has no grant for the key the connection uses. Assign the key in **API Keys → Assigned API keys**. |
| Aether closed or crashed mid-project | Start `Aether.cmd` again and sign in with the account that joined the network; hosting resumes on its own. A turn that was in flight is run again without repeating its recorded actions. |
| `Uninstall.cmd` refuses to run | Run it from the extracted ZIP folder, not the installed folder: `Uninstall.cmd --destination <installed folder>`. Your data is kept. |
| Installation fails on a long path | Extract and install to a short folder near the drive root, such as `C:\aether`. |
| A backup command asks for a hash | Each step prints the hash the next step needs. **Settings → Backup & Recovery** writes the exact commands for you. |

Still stuck? Open an issue with the steps, what you expected and what happened. Leave out keys, passwords and private
project content.
