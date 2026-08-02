# Running Ollama on Windows as a reliable server

If your GPU lives in a Windows box and famulus runs elsewhere (a Pi, a NAS),
you need Ollama to listen on the LAN and to stay up without anyone logging in.

## 1. Listen on the network

By default Ollama only accepts connections from localhost.

```powershell
setx /M OLLAMA_HOST 0.0.0.0
setx /M OLLAMA_MODELS C:\ollama\models        # optional: keep models off C:\Users
New-NetFirewallRule -DisplayName "Ollama LAN" -Direction Inbound -Protocol TCP `
  -LocalPort 11434 -RemoteAddress 192.168.1.0/24 -Action Allow
```

Restrict `-RemoteAddress` to your own subnet. **Ollama has no authentication**
— anything that can reach port 11434 can use your GPU and read your models.
Never expose it to the internet.

## 2. Start it at boot, as a service-like task

```powershell
schtasks /Create /TN OllamaServe /SC ONSTART /RU SYSTEM /RL HIGHEST /F `
  /TR "\"C:\Users\<you>\AppData\Local\Programs\Ollama\ollama.exe\" serve"
```

## 3. ⚠️ Remove the 72-hour execution limit

**This is the step everyone misses.** Task Scheduler applies a default
*"Stop the task if it runs longer than 3 days"* to new tasks. Three days after
it starts, Windows kills Ollama — and because the trigger is *at startup*, it
will not restart until you reboot. The server just quietly disappears.

Symptoms: your bot reports it cannot reach the model; the Windows machine is
up and reachable; port 11434 is closed; `schtasks /Query /TN OllamaServe /V /FO LIST`
shows **`Last Result: 267014`** (`0x41306`, *SCHED_S_TASK_TERMINATED*).

Fix it, and add a watchdog that restarts Ollama within ten minutes if it ever
dies for any other reason:

```powershell
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -RestartInterval (New-TimeSpan -Minutes 2) `
    -RestartCount 999 -StartWhenAvailable

$atBoot   = New-ScheduledTaskTrigger -AtStartup
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) `
              -RepetitionInterval (New-TimeSpan -Minutes 10)

Set-ScheduledTask -TaskName OllamaServe -Trigger @($atBoot,$watchdog) -Settings $settings
```

`MultipleInstances IgnoreNew` makes the ten-minute trigger a no-op while Ollama
is already running, so it only acts as a restart.

Verify with:

```powershell
(Get-ScheduledTask -TaskName OllamaServe).Settings.ExecutionTimeLimit   # PT0S = unlimited
```

## 4. Belt and braces: configure a fallback

Even with the above, the machine can be asleep or rebooting. Give famulus a
second backend so it degrades instead of failing:

```bash
LLM_BACKENDS=http://192.168.1.50:11434|qwen3:8b,http://localhost:11434|qwen3:4b
```
