# Install Aether with an AI assistant

**For the AI.** The person has unzipped this package and asked you to install Aether on this Windows computer by
following this file. Do the steps in order. The person only takes over the browser for their own password and API key.

## Rules (read these first)

1. **Use PowerShell and copy each command exactly.** Change nothing in it except where a step says to put in a value
   you recorded.
2. **Stop at the first error.** If a command fails or its result doesn't match what the step expects, stop. Show the
   person the exact command and its full output, and don't try another route on your own.
3. **Never type, ask for, read or repeat the person's API key or their new password.** They type those into the
   welcome screen themselves. You may type the default sign-in (`admin` / `aether-admin`): it's printed publicly and
   is the same on every install.
4. **Change nothing in this package folder, and delete nothing anywhere.** The package folder is needed later to
   uninstall.
5. **Run Aether only once.** If it's already running (step 1 tells you), don't start a second copy.
6. **Tell the person what you're doing in one line before each step.**

## Step 1: Find the package, and check what is already here

Run this in the folder the person unzipped (the one that holds this file):

```powershell
$pkg = (Get-Location).Path
'Install.cmd','Aether.cmd','aether.py','bundle.json','runtime\python.exe' | ForEach-Object { "{0} {1}" -f (Test-Path (Join-Path $pkg $_)), $_ }
Test-Path "$env:LOCALAPPDATA\Programs\Aether\preview\installation.json"
Test-Path "$env:USERPROFILE\.a\.aether_control\installed-instance.json"
```

**Expect:** five lines that start with `True`. If any says `False`, you're in the wrong folder: ask the person where
they unzipped the package and `Set-Location` there.

- The next line is `True` if Aether is already installed. Then installing again **updates** it: the application is
  replaced and the person's data is kept. Ask the person: "Aether is already installed here. Update it?" Continue only
  on a yes.
- The last line is `True` if Aether has run before. Ask the person to close any open Aether console window (closing
  it stops Aether) before you continue.

## Step 2: Install

```powershell
$receipt = & "$pkg\runtime\python.exe" -I -B "$pkg\aether.py" install --json | ConvertFrom-Json
$app = $receipt.installed; $data = $receipt.data
"Application: $app"; "Data: $data"
```

**Expect:** two lines with folders, normally `Programs\Aether\preview` under `%LOCALAPPDATA%`, and `.a` under
`%USERPROFILE%`.
Keep `$app` and `$data` for the next steps. If the command printed `Aether: ...` instead, that is the error: stop.

**If the person has data from another Aether copy to bring over, this is when to do it** (after installing, before
the first start). Follow the instructions the person gives you for that, then continue with step 3.

## Step 3: Start Aether in its own window

```powershell
Start-Process -FilePath "$app\Aether.cmd" -ArgumentList '--no-browser' -WorkingDirectory $app
$ready = $null
foreach ($i in 1..90) {
  Start-Sleep -Seconds 1
  try { $ready = Get-Content "$data\.aether_control\installed-instance.json" -Raw | ConvertFrom-Json } catch { }
  if ($ready.status -eq 'READY') { break }
}
$url = $ready.url; "Aether: $url"
(Invoke-WebRequest "$url/api/health" -UseBasicParsing).StatusCode
```

**Expect:** `Aether: http://127.0.0.1:8484` (or another port), then `200`. A new console window has opened. Tell the
person: **"Leave that window open. Closing it stops Aether."** On the very first start that window also shows the
default sign-in.

If there is no `READY` after 90 seconds, show the person what the new console window says and stop.

## Step 4: The person's welcome (they type; you wait)

Give the person exactly this, with the address from step 3:

> Open **<the address>** in your browser and sign in with username **admin**, password **aether-admin**.
> A welcome screen opens. Type your own new password (at least 12 characters). Then choose your AI provider
> (DeepSeek, Claude or OpenAI) and paste its API key if you have one. Press **Finish setup** and tell me when you're
> done.

If you can drive a browser, you may open the address and sign in as `admin` / `aether-admin` for them, then hand the
page to the person at the welcome screen. Don't type into the welcome screen's fields.

If this computer already had accounts (an update, or data brought over), there is no default Admin and no welcome
screen: the person signs in with their own account. Skip to step 6.

## Step 5: Check the welcome worked

When the person says they're done:

```powershell
Test-Path "$data\.aether_control\operators\welcome-pending.json"
(Get-Content "$data\b\runtime_profiles.json" -Raw | ConvertFrom-Json).profiles.PSObject.Properties | ForEach-Object { "$($_.Name)  $($_.Value.endpoint)  $($_.Value.requestedApiIdentifier)" }
```

**Expect:** `False` (the welcome is done), then a line for the provider the person chose: `deepseek-flash` at
`https://api.deepseek.com`, `claude-opus-5-5` at `https://api.anthropic.com/v1`, or `gpt-5.5` at
`https://api.openai.com/v1`. The `unconfigured` line is a placeholder and is always there. If the person skipped the
key, there is no provider line: that's fine, they can add it later under Settings → API Keys and Settings → Models.
These files hold no key: the key itself is encrypted in a separate folder you never open.

If the person wants a model on this computer instead (LM Studio or llama.cpp), they add it under Settings → Models →
This computer; it needs no key.

## Step 6: Report to the person

Tell the person, in a few lines:

- the address of Aether, and that its console window must stay open while they use it;
- the application folder (`$app`) and the data folder (`$data`);
- whether the welcome was done, and which provider's connection exists;
- **to stop Aether:** close its console window; **to start it again:** double-click `Aether.cmd` in `$app`;
- **to uninstall:** run `Uninstall.cmd` from this unzipped package folder. Their data and keys are kept.

The full human guide is `PUBLIC_SETUP.md` in this folder.
